"""插件清单 — plugin.json 解析与校验

plugin.json 是插件元数据的单一事实来源（id/kind/触发声明/钩子优先级/权限/
配置默认值等），加载失败仅跳过该插件并记录 error，不影响其他插件。

清单字段与目录布局说明见 config/plugins/README.md。
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..logging import get_logger

_logger = get_logger("pty-plugins")

# 合法形态：process=进程级（daemon 常驻，接管消息类型）、
# session=会话级（挂载到会话）、cli=CLI 侧（客户端进程内执行）
VALID_KINDS = ("process", "session", "cli")
VALID_TRIGGERS = ("event", "poll")
VALID_AUTOLOAD_KEYS = ("command", "cwd", "env")

MANIFEST_FILE = "plugin.json"
SCHEMA_FILE = "config.schema.json"
ENTRY_FILE = "__init__.py"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass
class PluginManifest:
    """插件清单（校验后的运行时视图）"""

    id: str
    version: str
    kind: str
    path: str                      # 插件目录绝对路径
    description: str = ""
    entry: str = ENTRY_FILE
    triggers: List[str] = field(default_factory=list)
    poll_interval: Optional[float] = None
    auto_load: Optional[dict] = None
    message_types: List[str] = field(default_factory=list)
    needs_io: bool = False
    commands: List[str] = field(default_factory=list)
    hooks: Dict[str, dict] = field(default_factory=dict)   # 钩子名 → {priority}
    permissions: List[str] = field(default_factory=list)   # 必需能力
    config_defaults: Dict[str, Any] = field(default_factory=dict)
    config_schema: Optional[dict] = None
    events: List[str] = field(default_factory=list)        # 订阅的主题模式
    dependencies: Dict[str, dict] = field(default_factory=dict)  # {plugins/python}


def _fail(manifest_file: str, msg: str) -> None:
    _logger.error("插件清单校验失败 %s: %s", manifest_file, msg)


def _validate(data: dict, manifest_file: str) -> bool:
    """清单结构校验；非法时记 error 并返回 False"""
    plugin_id = data.get("id")
    if not isinstance(plugin_id, str) or _ID_RE.match(plugin_id) is None:
        _fail(manifest_file, "id 必须为小写字母/数字/下划线/连字符组合")
        return False
    if not isinstance(data.get("version"), str) or not data["version"]:
        _fail(manifest_file, "version 必须为非空字符串")
        return False
    if data.get("kind") not in VALID_KINDS:
        _fail(manifest_file, "kind 必须为 %s 之一" % "/".join(VALID_KINDS))
        return False
    kind = data["kind"]
    if data.get("entry") is not None and not isinstance(data["entry"], str):
        _fail(manifest_file, "entry 必须为字符串")
        return False

    # 触发声明（仅 session 形态合法）
    triggers = data.get("triggers", [])
    if not isinstance(triggers, list) or any(t not in VALID_TRIGGERS for t in triggers):
        _fail(manifest_file, "triggers 只能包含 %s" % "/".join(VALID_TRIGGERS))
        return False
    if kind != "session" and triggers:
        _fail(manifest_file, "triggers 仅 session 形态可用（当前 kind=%s）" % kind)
        return False
    if "poll" in triggers:
        interval = data.get("pollInterval")
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
            _fail(manifest_file, "声明 poll 必须提供正数 pollInterval")
            return False

    # 自动加载（仅 session 形态合法；键拼写错误会静默"匹配一切"，须校验）
    auto_load = data.get("autoLoad")
    if auto_load is not None:
        if not isinstance(auto_load, dict):
            _fail(manifest_file, "autoLoad 必须为对象")
            return False
        unknown = set(auto_load) - set(VALID_AUTOLOAD_KEYS)
        if unknown:
            _fail(manifest_file, "autoLoad 含未知键 %s (合法: %s)" % (
                sorted(unknown), "/".join(VALID_AUTOLOAD_KEYS)))
            return False
        if kind != "session":
            _fail(manifest_file, "autoLoad 仅 session 形态可用（当前 kind=%s）" % kind)
            return False
        rule_cmd = auto_load.get("command")
        if rule_cmd is not None and not isinstance(rule_cmd, (str, list)):
            _fail(manifest_file, "autoLoad.command 必须为 str(正则) 或 list(关键词)")
            return False
        if auto_load.get("cwd") is not None and not isinstance(auto_load["cwd"], list):
            _fail(manifest_file, "autoLoad.cwd 必须为 list")
            return False
        if auto_load.get("env") is not None and not isinstance(auto_load["env"], dict):
            _fail(manifest_file, "autoLoad.env 必须为对象")
            return False

    # 消息类型（仅 process 形态合法）
    message_types = data.get("messageTypes", [])
    if not isinstance(message_types, list) or any(
        not isinstance(t, str) or not t for t in message_types
    ):
        _fail(manifest_file, "messageTypes 必须为非空字符串列表")
        return False
    if kind != "process" and message_types:
        _fail(manifest_file, "messageTypes 仅 process 形态可用（当前 kind=%s）" % kind)
        return False
    if data.get("needsIO") is not None and not isinstance(data["needsIO"], bool):
        _fail(manifest_file, "needsIO 必须为布尔")
        return False

    # 命令白名单（CLI 形态）
    commands = data.get("commands", [])
    if not isinstance(commands, list) or any(
        not isinstance(c, str) or not c for c in commands
    ):
        _fail(manifest_file, "commands 必须为非空字符串列表")
        return False

    # 钩子优先级声明
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        _fail(manifest_file, "hooks 必须为对象")
        return False
    for hook, decl in hooks.items():
        if not isinstance(hook, str) or not isinstance(decl, dict):
            _fail(manifest_file, "hooks 值必须为对象（含可选 priority）")
            return False
        priority = decl.get("priority", 100)
        if not isinstance(priority, int) or isinstance(priority, bool):
            _fail(manifest_file, "hooks.%s.priority 必须为整数" % hook)
            return False

    # 权限声明
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict) or not isinstance(
        permissions.get("required", []), list
    ) or any(not isinstance(p, str) or not p for p in permissions.get("required", [])):
        _fail(manifest_file, "permissions.required 必须为非空字符串列表")
        return False

    # 配置默认值
    config = data.get("config", {})
    if not isinstance(config, dict) or not isinstance(config.get("defaults", {}), dict):
        _fail(manifest_file, "config.defaults 必须为对象")
        return False

    # 事件订阅
    events = data.get("events", {})
    if not isinstance(events, dict) or not isinstance(events.get("subscribe", []), list):
        _fail(manifest_file, "events.subscribe 必须为列表")
        return False

    # 依赖声明
    deps = data.get("dependencies", {})
    if not isinstance(deps, dict):
        _fail(manifest_file, "dependencies 必须为对象")
        return False
    for scope in ("plugins", "python"):
        if scope in deps and not isinstance(deps[scope], dict):
            _fail(manifest_file, "dependencies.%s 必须为对象" % scope)
            return False

    return True


def load_manifest(plugin_dir: str) -> Optional[PluginManifest]:
    """读取并校验插件目录的 plugin.json；失败返回 None（错误已记日志）"""
    manifest_file = os.path.join(plugin_dir, MANIFEST_FILE)
    try:
        with open(manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        _logger.warning("插件目录缺少 %s，跳过: %s", MANIFEST_FILE, plugin_dir)
        return None
    except (OSError, ValueError) as e:
        _logger.error("插件清单读取失败 %s: %s", manifest_file, e)
        return None
    if not isinstance(data, dict):
        _fail(manifest_file, "顶层必须为对象")
        return None
    if not _validate(data, manifest_file):
        return None

    schema = None
    schema_file = os.path.join(plugin_dir, SCHEMA_FILE)
    if os.path.isfile(schema_file):
        try:
            with open(schema_file, "r", encoding="utf-8") as f:
                schema = json.load(f)
            if not isinstance(schema, dict):
                _logger.error("配置 schema 顶层必须为对象: %s", schema_file)
                schema = None
        except (OSError, ValueError) as e:
            _logger.error("配置 schema 读取失败 %s: %s", schema_file, e)

    manifest = PluginManifest(
        id=data["id"],
        version=data["version"],
        kind=data["kind"],
        path=os.path.abspath(plugin_dir),
        description=data.get("description", ""),
        entry=data.get("entry", ENTRY_FILE),
        triggers=list(data.get("triggers", [])),
        poll_interval=data.get("pollInterval"),
        auto_load=data.get("autoLoad"),
        message_types=list(data.get("messageTypes", [])),
        needs_io=bool(data.get("needsIO", False)),
        commands=list(data.get("commands", [])),
        hooks={k: dict(v) for k, v in data.get("hooks", {}).items()},
        permissions=list(data.get("permissions", {}).get("required", [])),
        config_defaults=dict(data.get("config", {}).get("defaults", {})),
        config_schema=schema,
        events=list(data.get("events", {}).get("subscribe", [])),
        dependencies={k: dict(v) for k, v in data.get("dependencies", {}).items()},
    )
    return manifest
