"""插件加载器 — 目录扫描、模块加载与声明校验

扫描插件目录（单文件 *.py 或含 __init__.py 的子目录），按路径 importlib 加载。
约定：模块必须导出 `plugin` 属性（Plugin 实例或 Plugin 子类）。
校验失败（缺声明/声明冲突/触发声明非法）仅跳过该插件，不影响其他插件加载。
"""

import importlib.util
import os
from typing import List, Optional

from .base import VALID_KINDS, VALID_TRIGGERS, Plugin
from ..logging import get_logger

_logger = get_logger("pty-plugins")


def resolve_plugin_paths(plugin_paths: List[str]) -> List[str]:
    """规范化插件位置列表（去重、剔除不存在的路径）

    插件位置由配置显式指定（JSON 或环境变量），不再扫描目录。
    """
    seen = set()
    resolved = []
    for p in plugin_paths:
        if not p:
            continue
        if p in seen:
            continue
        seen.add(p)
        if not os.path.exists(p):
            _logger.warning("插件位置不存在，跳过: %s", p)
            continue
        resolved.append(p)
    return resolved


def load_module(module_path: str):
    """按文件路径加载插件模块（唯一模块名，避免与业务包冲突）"""
    if os.path.isdir(module_path):
        spec_file = os.path.join(module_path, "__init__.py")
        mod_name = "pty_plugin_" + os.path.basename(module_path.rstrip(os.sep))
    else:
        spec_file = module_path
        mod_name = "pty_plugin_" + os.path.splitext(os.path.basename(module_path))[0]
    spec = importlib.util.spec_from_file_location(mod_name, spec_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法创建模块规格: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_plugin_class(module, module_path: str) -> Optional[type]:
    """提取模块导出的插件类；无导出或导出非法时返回 None"""
    attr = getattr(module, "plugin", None)
    if attr is None:
        _logger.error("插件模块缺少 plugin 导出: %s", module_path)
        return None
    cls = attr if isinstance(attr, type) else type(attr)
    if not (isinstance(attr, (Plugin, type)) and issubclass(cls, Plugin)):
        _logger.error("插件模块 plugin 导出非 Plugin: %s", module_path)
        return None
    return cls


def resolve_kind(cls: type) -> str:
    """解析插件形态：显式声明优先，否则按 message_types 推断"""
    if cls.kind:
        return cls.kind
    return "process" if cls.message_types else "session"


def validate_plugin(cls: type) -> bool:
    """校验插件声明；非法时记录 error 日志并返回 False"""
    name = cls.name or "<未命名>"

    kind = resolve_kind(cls)
    if kind not in VALID_KINDS:
        _logger.error(
            "插件 %s: kind 非法 %r (合法: %s)", name, kind, VALID_KINDS
        )
        return False

    # CLI 形态钩子校验：必须实现至少一个 CLI 钩子
    if kind == "cli":
        has_cli_hook = any(
            getattr(cls, hook) is not getattr(Plugin, hook)
            for hook in ("before_request", "transform_response", "render_response")
        )
        if not has_cli_hook:
            _logger.error(
                "插件 %s: kind=cli 但未实现任何 CLI 钩子 "
                "(before_request/transform_response/render_response)",
                name,
            )
            return False
        if not isinstance(cls.commands, (list, tuple)):
            _logger.error("插件 %s: commands 必须为列表", name)
            return False
        # CLI 形态不参与 daemon 挂载/消息路由，跳过 daemon 侧钩子声明校验
        return True

    # triggers 可为空（无事件/定时触发的纯钩子插件：inspect_state/handle_command）
    if not isinstance(cls.triggers, (list, tuple)):
        _logger.error("插件 %s: triggers 必须为列表", name)
        return False

    invalid = set(cls.triggers) - set(VALID_TRIGGERS)
    if invalid:
        _logger.error(
            "插件 %s: triggers 含非法值 %r (合法: %s)",
            name,
            sorted(invalid),
            VALID_TRIGGERS,
        )
        return False

    if "poll" in cls.triggers:
        interval = cls.poll_interval
        if (
            not isinstance(interval, (int, float))
            or isinstance(interval, bool)
            or interval <= 0
        ):
            _logger.error("插件 %s: 声明 poll 但 poll_interval 缺失或非法", name)
            return False
        if cls.on_poll is Plugin.on_poll:
            _logger.error("插件 %s: 声明 poll 但未实现 on_poll", name)
            return False

    if "event" in cls.triggers and cls.on_event is Plugin.on_event:
        _logger.error("插件 %s: 声明 event 但未实现 on_event", name)
        return False

    # 进程级插件声明校验：message_types 非空须实现 handle_message；needs_io 须为 bool
    if not isinstance(cls.message_types, (list, tuple)):
        _logger.error("插件 %s: message_types 必须为列表", name)
        return False
    if any(not isinstance(t, str) or not t for t in cls.message_types):
        _logger.error("插件 %s: message_types 含非法项 %r", name, cls.message_types)
        return False
    if cls.message_types and cls.handle_message is Plugin.handle_message:
        _logger.error("插件 %s: 声明 message_types 但未实现 handle_message", name)
        return False
    if not isinstance(cls.needs_io, bool):
        _logger.error("插件 %s: needs_io 必须为 bool", name)
        return False

    # auto_load 结构校验：非 None 须为 dict，键仅限 command/cwd/env，
    # 各维度类型须匹配（command: str|list, cwd: list, env: dict）。
    # 拼写错误的键（如 cmd）若不校验会令 _match_auto_load 跳过所有维度
    # 而"匹配一切"，将插件静默注入每个会话。
    if cls.auto_load is not None:
        if not isinstance(cls.auto_load, dict):
            _logger.error("插件 %s: auto_load 必须为 dict 或 None", name)
            return False
        unknown = set(cls.auto_load) - {"command", "cwd", "env"}
        if unknown:
            _logger.error(
                "插件 %s: auto_load 含未知键 %r (合法: command/cwd/env)",
                name,
                sorted(unknown),
            )
            return False
        rule_cmd = cls.auto_load.get("command")
        if rule_cmd is not None and not isinstance(rule_cmd, (str, list)):
            _logger.error(
                "插件 %s: auto_load.command 必须为 str(正则) 或 list(关键词)", name
            )
            return False
        rule_cwd = cls.auto_load.get("cwd")
        if rule_cwd is not None and not isinstance(rule_cwd, list):
            _logger.error("插件 %s: auto_load.cwd 必须为 list", name)
            return False
        rule_env = cls.auto_load.get("env")
        if rule_env is not None and not isinstance(rule_env, dict):
            _logger.error("插件 %s: auto_load.env 必须为 dict", name)
            return False

    return True


def load_plugins(plugin_paths: List[str]) -> List[type]:
    """加载显式指定的插件模块（单插件失败不影响其他）

    Args:
        plugin_paths: 插件位置列表（单文件 *.py 或含 __init__.py 的子目录）。
    """
    classes = []
    for path in resolve_plugin_paths(plugin_paths):
        try:
            module = load_module(path)
            cls = extract_plugin_class(module, path)
            if cls is None:
                continue
            if not validate_plugin(cls):
                continue
            # 未显式声明 name 时默认取模块名（单文件取文件名，目录取目录名）
            if not cls.name:
                if os.path.isdir(path):
                    cls.name = os.path.basename(path.rstrip(os.sep))
                else:
                    cls.name = os.path.splitext(os.path.basename(path))[0]
            classes.append(cls)
        except Exception:
            _logger.exception("插件加载失败: %s", path)
    return classes
