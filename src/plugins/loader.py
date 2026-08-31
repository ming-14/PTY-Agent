"""插件加载器 — 清单驱动：目录 → plugin.json → 模块导入 → 声明校验

约定：
- 每个插件目录必须含 plugin.json（manifest.py 校验）
- 入口模块（默认 __init__.py）必须导出 `plugin`（Plugin 实例或子类）
- 清单声明的触发方式/钩子必须在类中实现；校验失败仅跳过该插件

加载成功后把清单字段注入类属性（name/version/description/kind/manifest），
供注册表/宿主/调度器统一经实例读取。
"""

import importlib.util
import os
import sys
from dataclasses import dataclass
from typing import List, Optional

from .base import Plugin, VALID_HOOKS
from .manifest import ENTRY_FILE, PluginManifest, load_manifest
from ..logging import get_logger

_logger = get_logger("pty-plugins")


@dataclass
class LoadedPlugin:
    """加载结果：清单 + 插件类 + 可选 CLI 命令类列表"""

    manifest: PluginManifest
    cls: type
    command_classes: List[type] = None  # 插件导出的 CLI Command 子类（kind 含 cli 时）


def module_name(plugin_id: str) -> str:
    """插件模块唯一名（避免与业务包冲突；reload 时据此清理 sys.modules）"""
    return "pty_plugin_" + plugin_id


def load_module(entry_path: str, mod_name: str):
    """按文件路径加载插件入口模块（唯一模块名）"""
    spec = importlib.util.spec_from_file_location(mod_name, entry_path)
    if spec is None or spec.loader is None:
        raise ImportError("无法创建模块规格: %s" % entry_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def extract_plugin_class(module, entry_path: str) -> Optional[type]:
    """提取模块导出的插件类；无导出或导出非法返回 None"""
    attr = getattr(module, "plugin", None)
    if attr is None:
        _logger.error("插件模块缺少 plugin 导出: %s", entry_path)
        return None
    cls = attr if isinstance(attr, type) else type(attr)
    if not isinstance(attr, Plugin) and not (
        isinstance(attr, type) and issubclass(cls, Plugin)
    ):
        _logger.error("插件模块 plugin 导出非 Plugin: %s", entry_path)
        return None
    return cls


def validate_plugin(cls: type, manifest: PluginManifest) -> bool:
    """清单-实现一致性校验；非法时记录 error 并返回 False"""
    name = manifest.id
    kinds = manifest.kind  # List[str]

    # CLI 钩子：声明 cliCommands 或实现 CLI 钩子时校验实现
    has_cli = any(
        getattr(cls, hook) is not getattr(Plugin, hook)
        for hook in ("check_request", "before_request", "transform_response", "render_response")
    )
    if manifest.cli_commands and not has_cli:
        _logger.error("插件 %s: 声明 cliCommands 但未实现任何 CLI 钩子", name)
        return False
    if "cli" in kinds and not has_cli:
        _logger.error("插件 %s: cli 形态但未实现任何 CLI 钩子", name)
        return False

    # 触发声明必须实现对应钩子
    if "event" in manifest.triggers and getattr(cls, "on_event") is getattr(
        Plugin, "on_event"
    ):
        _logger.error("插件 %s: 声明 event 但未实现 on_event", name)
        return False
    if "poll" in manifest.triggers and getattr(cls, "on_poll") is getattr(
        Plugin, "on_poll"
    ):
        _logger.error("插件 %s: 声明 poll 但未实现 on_poll", name)
        return False

    # 消息类型声明必须实现 handle_message
    if manifest.message_types and getattr(cls, "handle_message") is getattr(
        Plugin, "handle_message"
    ):
        _logger.error("插件 %s: 声明 messageTypes 但未实现 handle_message", name)
        return False

    # 响应装饰声明必须实现 decorate_response
    if manifest.decorate_types and getattr(cls, "decorate_response") is getattr(
        Plugin, "decorate_response"
    ):
        _logger.error("插件 %s: 声明 decorateTypes 但未实现 decorate_response", name)
        return False

    # 清单 hooks 声明的钩子必须已实现
    for hook in manifest.hooks:
        if hook not in VALID_HOOKS:
            _logger.error("插件 %s: hooks 声明未知钩子 %r", name, hook)
            return False
        if getattr(cls, hook, None) is getattr(Plugin, hook, None):
            _logger.error("插件 %s: hooks 声明 %s 但未实现", name, hook)
            return False

    return True


def extract_command_classes(module, entry_path: str) -> Optional[List[type]]:
    """提取模块导出的 CLI Command 子类列表（`commands` 属性，含 name/run 即视为 Command）；无导出返回 []"""
    attr = getattr(module, "commands", None)
    if attr is None:
        return []
    if not isinstance(attr, (list, tuple)):
        _logger.error("插件模块 commands 导出必须为列表: %s", entry_path)
        return None
    classes = []
    for item in attr:
        cls = item if isinstance(item, type) else type(item)
        if not hasattr(cls, "name") or not hasattr(cls, "run"):
            _logger.error("插件模块 commands 导出项缺少 name/run 属性: %s", entry_path)
            return None
        classes.append(cls)
    return classes


def validate_command_decl(manifest: PluginManifest, command_classes: List[type]) -> bool:
    """cliCommands 声明与导出的 Command 类名一致；非法记录 error 并返回 False"""
    declared = set(manifest.cli_commands)
    exported = {c.name for c in command_classes}
    if declared != exported:
        _logger.error(
            "插件 %s: cliCommands 声明 %s 与导出的命令 %s 不一致",
            manifest.id, sorted(declared), sorted(exported),
        )
        return False
    return True


def load_plugin_dir(plugin_dir: str) -> Optional[LoadedPlugin]:
    """加载单个插件目录；失败返回 None（错误已记日志）"""
    manifest = load_manifest(plugin_dir)
    if manifest is None:
        return None
    entry_path = os.path.join(plugin_dir, manifest.entry)
    if not os.path.isfile(entry_path):
        _logger.error("插件 %s: 入口文件缺失: %s", manifest.id, entry_path)
        return None
    mod_name = module_name(manifest.id)
    sys.modules.pop(mod_name, None)  # reload 场景清理旧模块
    try:
        module = load_module(entry_path, mod_name)
        cls = extract_plugin_class(module, entry_path)
        if cls is None:
            return None
        if not validate_plugin(cls, manifest):
            return None
        command_classes = extract_command_classes(module, entry_path)
        if command_classes is None:
            return None
        if manifest.cli_commands and not validate_command_decl(manifest, command_classes):
            return None
    except Exception:
        _logger.exception("插件加载失败: %s", plugin_dir)
        return None
    # 清单字段注入类属性（实例经类继承）
    cls.name = manifest.id
    cls.version = manifest.version
    cls.description = manifest.description
    cls.kind = manifest.kind
    cls.manifest = manifest
    _logger.info(
        "插件已加载: %s v%s kind=%s 触发=%s 消息=%s io=%s 命令=%s",
        manifest.id,
        manifest.version,
        "/".join(manifest.kind),
        list(manifest.triggers),
        list(manifest.message_types),
        manifest.needs_io,
        list(manifest.cli_commands),
    )
    return LoadedPlugin(
        manifest=manifest,
        cls=cls,
        command_classes=command_classes or None,
    )


def load_plugins(plugin_dirs: List[str]) -> List[LoadedPlugin]:
    """加载多个插件目录（单插件失败不影响其他）"""
    loaded = []
    for plugin_dir in plugin_dirs:
        item = load_plugin_dir(plugin_dir)
        if item is not None:
            loaded.append(item)
    return loaded