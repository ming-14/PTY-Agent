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
_OPTION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHORT_RE = re.compile(r"^[a-zA-Z0-9]$")

# 插件 CLI 选项的 argparse 类型（值类型映射见 cli_options.py）
VALID_OPTION_TYPES = ("str", "int", "float", "flag", "choice")

# 可承载插件 CLI 选项的命令集（会话 IO 命令；cli_options.py 同源复用）
CLI_OPTION_COMMANDS = ("exec", "send", "advsend", "read", "mouse")


@dataclass
class PluginCliOption:
    """插件自定义 CLI 选项声明（plugin.json cliOptions 项）"""

    name: str                       # 选项名 → 长选项 --<name>
    short: Optional[str] = None     # 短选项字符 → 短选项 -<short>
    type: str = "str"               # str/int/float/flag/choice
    choices: Optional[List[str]] = None
    default: Any = None
    help: str = ""
    commands: List[str] = field(default_factory=list)  # 生效命令；空=全部

    def to_dict(self) -> dict:
        """序列化为清单原样形状（plugin info 展示用）"""
        return {
            "name": self.name,
            "short": self.short,
            "type": self.type,
            "choices": list(self.choices) if self.choices else None,
            "default": self.default,
            "help": self.help,
            "commands": list(self.commands),
        }


@dataclass
class PluginManifest:
    """插件清单（校验后的运行时视图）"""

    id: str
    version: str
    kind: List[str]                # 形态集合：process/session/cli（多 kind 组合）
    path: str                      # 插件目录绝对路径
    description: str = ""
    entry: str = ENTRY_FILE
    triggers: List[str] = field(default_factory=list)
    poll_interval: Optional[float] = None
    auto_load: Optional[dict] = None
    message_types: List[str] = field(default_factory=list)
    needs_io: bool = False
    commands: List[str] = field(default_factory=list)
    cli_commands: List[str] = field(default_factory=list)  # 注册的 CLI 命令名
    decorate_types: List[str] = field(default_factory=list)  # 装饰的内置命令响应类型
    auto_mount: List[str] = field(default_factory=list)  # CLI 形态：命令自动参与钩子
    context_hidden: bool = False  # 隐藏上下文（daemon 启动时不自动输出，plugin gethelp 按需查看）
    hooks: Dict[str, dict] = field(default_factory=dict)   # 钩子名 → {priority}
    permissions: List[str] = field(default_factory=list)   # 必需能力
    config_defaults: Dict[str, Any] = field(default_factory=dict)
    config_schema: Optional[dict] = None
    events: List[str] = field(default_factory=list)        # 订阅的主题模式
    dependencies: Dict[str, dict] = field(default_factory=dict)  # {plugins/python}
    cli_options: List[PluginCliOption] = field(default_factory=list)  # 自定义 CLI 选项


def _fail(manifest_file: str, msg: str) -> None:
    _logger.error("插件清单校验失败 %s: %s", manifest_file, msg)


def _validate(data: dict, manifest_file: str) -> bool:
    """清单结构校验；非法时记 error 并返回 False"""
    plugin_id = data.get("id")
    if (
        not isinstance(plugin_id, str)
        or _ID_RE.match(plugin_id) is None
        or len(plugin_id) > 64
    ):
        _fail(manifest_file, "id 必须为小写字母/数字/下划线/连字符组合（长度 ≤ 64）")
        return False
    if not isinstance(data.get("version"), str) or not data["version"]:
        _fail(manifest_file, "version 必须为非空字符串")
        return False
    kind_raw = data.get("kind")
    if isinstance(kind_raw, str):
        kinds = [kind_raw]
    elif isinstance(kind_raw, list) and kind_raw:
        kinds = kind_raw[:]
    else:
        _fail(manifest_file, "kind 必须为 %s 之一或它们的组合" % "/".join(VALID_KINDS))
        return False
    for k in kinds:
        if k not in VALID_KINDS:
            _fail(manifest_file, "kind 元素 '%s' 非法，必须为 %s 之一" % (k, "/".join(VALID_KINDS)))
            return False
    if len(set(kinds)) != len(kinds):
        _fail(manifest_file, "kind 含重复元素")
        return False
    kind = kinds[0]  # 主形态，用于部分兼容判断
    if data.get("entry") is not None and not isinstance(data["entry"], str):
        _fail(manifest_file, "entry 必须为字符串")
        return False

    # 触发声明
    triggers = data.get("triggers", [])
    if not isinstance(triggers, list) or any(t not in VALID_TRIGGERS for t in triggers):
        _fail(manifest_file, "triggers 只能包含 %s" % "/".join(VALID_TRIGGERS))
        return False
    if "poll" in triggers:
        interval = data.get("pollInterval")
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
            _fail(manifest_file, "声明 poll 必须提供正数 pollInterval")
            return False

    # 自动加载（键拼写错误会静默"匹配一切"，须校验）
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

    # 消息类型
    message_types = data.get("messageTypes", [])
    if not isinstance(message_types, list) or any(
        not isinstance(t, str) or not t for t in message_types
    ):
        _fail(manifest_file, "messageTypes 必须为非空字符串列表")
        return False
    if data.get("needsIO") is not None and not isinstance(data["needsIO"], bool):
        _fail(manifest_file, "needsIO 必须为布尔")
        return False

    # 响应装饰类型
    decorate_types = data.get("decorateTypes", [])
    if not isinstance(decorate_types, list) or any(
        not isinstance(t, str) or not t for t in decorate_types
    ):
        _fail(manifest_file, "decorateTypes 必须为非空字符串列表")
        return False

    # 命令白名单（CLI 形态：钩子作用命令集）
    commands = data.get("commands", [])
    if not isinstance(commands, list) or any(
        not isinstance(c, str) or not c for c in commands
    ):
        _fail(manifest_file, "commands 必须为非空字符串列表")
        return False

    # 注册的新 CLI 命令名（CLI 形态：插件代码导出的 Command 类）
    cli_commands = data.get("cliCommands", [])
    if not isinstance(cli_commands, list) or any(
        not isinstance(c, str) or not c for c in cli_commands
    ):
        _fail(manifest_file, "cliCommands 必须为非空字符串列表")
        return False

    # 自动挂载（CLI 形态：命令自动参与钩子，无需 --plugin 显式激活）
    auto_mount = data.get("autoMount", [])
    if not isinstance(auto_mount, list) or any(
        not isinstance(c, str) or not c for c in auto_mount
    ):
        _fail(manifest_file, "autoMount 必须为字符串列表")
        return False

    # 上下文隐藏（contextHidden）：daemon 启动时不自动输出 <插件名>.md
    if data.get("contextHidden") is not None and not isinstance(data["contextHidden"], bool):
        _fail(manifest_file, "contextHidden 必须为布尔")
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

    # 自定义 CLI 选项（含 cli/session 形态可用）
    cli_options = data.get("cliOptions", [])
    if not _validate_cli_options(cli_options, kinds, manifest_file):
        return False

    return True


def _validate_cli_options(cli_options, kinds: List[str], manifest_file: str) -> bool:
    """cliOptions 结构校验；非法时记 error 并返回 False

    选项名/短选项在本插件内唯一；commands 必须是可承载命令之一；
    纯 process 形态禁止声明（其消息源命令不在会话 IO 命令集内，无注入路径）。
    """
    if not isinstance(cli_options, list):
        _fail(manifest_file, "cliOptions 必须为数组")
        return False
    if cli_options and set(kinds) == {"process"}:
        _fail(manifest_file, "纯 process 形态禁止声明 cliOptions（消息源命令不在会话 IO 命令集内）")
        return False
    names = []
    shorts = []
    for i, item in enumerate(cli_options):
        if not isinstance(item, dict):
            _fail(manifest_file, "cliOptions[%d] 必须为对象" % i)
            return False
        name = item.get("name")
        if (
            not isinstance(name, str)
            or _OPTION_NAME_RE.match(name) is None
            or len(name) > 64
        ):
            _fail(manifest_file, "cliOptions[%d].name 必须为小写字母/数字/连字符组合（长度 ≤ 64）" % i)
            return False
        if name in names:
            _fail(manifest_file, "cliOptions 选项名重复: %s" % name)
            return False
        names.append(name)
        short = item.get("short")
        if short is not None:
            if not isinstance(short, str) or _SHORT_RE.match(short) is None:
                _fail(manifest_file, "cliOptions[%d].short 必须为单个字母/数字" % i)
                return False
            if short in shorts:
                _fail(manifest_file, "cliOptions 短选项重复: -%s" % short)
                return False
            shorts.append(short)
        opt_type = item.get("type", "str")
        if opt_type not in VALID_OPTION_TYPES:
            _fail(manifest_file, "cliOptions[%d].type 必须为 %s 之一" % (
                i, "/".join(VALID_OPTION_TYPES)))
            return False
        choices = item.get("choices")
        if opt_type == "choice":
            if not isinstance(choices, list) or not choices or any(
                not isinstance(c, str) or not c for c in choices
            ):
                _fail(manifest_file, "cliOptions[%d] type=choice 必须提供非空字符串 choices" % i)
                return False
        elif choices is not None:
            _fail(manifest_file, "cliOptions[%d].choices 仅 type=choice 可用" % i)
            return False
        default = item.get("default")
        if not _default_matches_type(default, opt_type, choices):
            _fail(manifest_file, "cliOptions[%d].default 与 type=%s 不匹配" % (i, opt_type))
            return False
        help_ = item.get("help")
        if help_ is not None and not isinstance(help_, str):
            _fail(manifest_file, "cliOptions[%d].help 必须为字符串" % i)
            return False
        commands = item.get("commands")
        if commands is not None:
            if not isinstance(commands, list) or any(
                c not in CLI_OPTION_COMMANDS for c in commands
            ):
                _fail(manifest_file, "cliOptions[%d].commands 只能包含 %s" % (
                    i, "/".join(CLI_OPTION_COMMANDS)))
                return False
            if len(set(commands)) != len(commands):
                _fail(manifest_file, "cliOptions[%d].commands 含重复项" % i)
                return False
    return True


def _default_matches_type(default, opt_type: str, choices) -> bool:
    """选项默认值与类型匹配检查（缺省视为合法）"""
    if default is None:
        return True
    if opt_type == "str":
        return isinstance(default, str)
    if opt_type == "int":
        return isinstance(default, int) and not isinstance(default, bool)
    if opt_type == "float":
        return isinstance(default, (int, float)) and not isinstance(default, bool)
    if opt_type == "flag":
        return isinstance(default, bool)
    if opt_type == "choice":
        return isinstance(default, str) and default in (choices or [])
    return False


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

    # kind 归一化：字符串或数组 → 列表（与 _validate 一致）
    _kind_raw = data.get("kind")
    _kinds = [_kind_raw] if isinstance(_kind_raw, str) else list(_kind_raw)

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
        kind=list(_kinds),
        path=os.path.abspath(plugin_dir),
        description=data.get("description", ""),
        entry=data.get("entry", ENTRY_FILE),
        triggers=list(data.get("triggers", [])),
        poll_interval=data.get("pollInterval"),
        auto_load=data.get("autoLoad"),
        message_types=list(data.get("messageTypes", [])),
        needs_io=bool(data.get("needsIO", False)),
        commands=list(data.get("commands", [])),
        cli_commands=list(data.get("cliCommands", [])),
        decorate_types=list(data.get("decorateTypes", [])),
        auto_mount=list(data.get("autoMount", [])),
        context_hidden=bool(data.get("contextHidden", False)),
        hooks={k: dict(v) for k, v in data.get("hooks", {}).items()},
        permissions=list(data.get("permissions", {}).get("required", [])),
        config_defaults=dict(data.get("config", {}).get("defaults", {})),
        config_schema=schema,
        events=list(data.get("events", {}).get("subscribe", [])),
        dependencies={k: dict(v) for k, v in data.get("dependencies", {}).items()},
        cli_options=[
            _parse_option(item)
            for item in data.get("cliOptions", [])
        ],
    )
    return manifest


def _parse_option(item: dict) -> PluginCliOption:
    """将 cliOptions 数组项解析为 PluginCliOption（data 已校验，不做防御）"""
    return PluginCliOption(
        name=item["name"],
        short=item.get("short"),
        type=item.get("type", "str"),
        choices=list(item.get("choices", [])) if item.get("choices") else None,
        default=item.get("default"),
        help=item.get("help", ""),
        commands=list(item.get("commands", [])) if item.get("commands") else [],
    )
