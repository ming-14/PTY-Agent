"""插件自定义 CLI 选项 — 声明注册、冲突检测、值收集与消息校验

插件在 plugin.json 的 cliOptions 声明自己的 CLI 选项（结构校验见 manifest.py），
本模块提供 daemon 与客户端两侧共用的能力：

- CLI_OPTION_COMMANDS: 可承载插件选项的命令集（会话 IO 命令）
- RESERVED_OPTIONS:    各命令已注册的内置选项串（与 src/cli/commands/* 及
                       common_args.py 的 argparse 定义保持一致；由
                       tests/unit/plugins/test_cli_options.py 不变量测试
                       构建真实解析器比对，防止新增内置参数后表漂移）
- check_cli_option_conflicts: 全量冲突检测（内置/插件间，按命令域），
                       冲突插件不加载（daemon 置 BROKEN、客户端跳过）
- build_option_registrations: 生成 argparse 注册描述（仅非冲突插件）
- collect_option_values: 从 parse 结果收集显式提供的选项值
- validate_plugin_options: daemon 侧消息形状校验
"""

import argparse
import json
from typing import Dict, List, Optional

from .manifest import CLI_OPTION_COMMANDS, PluginCliOption, PluginManifest

# 内置选项串（长+短，含 argparse 自动生成的 -h/--help），与 src/cli/commands/*
# 及 src/cli/common_args.py 的 argparse 定义保持一致；不变量测试保证同步
RESERVED_OPTIONS: Dict[str, frozenset] = {
    "exec": frozenset({
        "-h", "--help",
        "-c", "--command", "--force-pty-mode",
        "-t", "--trigger", "--newline", "--timeout", "--idle-timeout",
        "--idle-after-first-output", "--keep-ansi", "-s", "--snapshot-diff",
        "--notify",
        "--full", "-l", "--lines",
        "-o", "--output", "--response-format", "--svg-compression-level",
        "--encoding", "--default", "--debug-output",
        "--cwd", "--env", "--subprocess", "--shell", "--size", "--plugin",
    }),
    "send": frozenset({
        "-h", "--help",
        "-i", "--input",
        "-t", "--trigger", "--newline", "--timeout", "--idle-timeout",
        "--idle-after-first-output", "--keep-ansi", "-s", "--snapshot-diff",
        "--notify",
        "--full", "-e", "--send-eol", "-l", "--lines",
        "-o", "--output", "--response-format", "--svg-compression-level",
        "--encoding", "--default", "--debug-output",
    }),
    "advsend": frozenset({
        "-h", "--help",
        "-i", "--input",
        "-t", "--trigger", "--newline", "--timeout", "--idle-timeout",
        "--idle-after-first-output", "--keep-ansi", "-s", "--snapshot-diff",
        "--notify",
        "--full", "-e", "--send-eol", "-l", "--lines",
        "-o", "--output", "--response-format", "--svg-compression-level",
        "--encoding", "--default", "--debug-output",
    }),
    "read": frozenset({
        "-h", "--help",
        "-t", "--trigger", "--newline", "--timeout", "--idle-timeout",
        "--idle-after-first-output", "--keep-ansi", "-s", "--snapshot-diff",
        "--notify",
        "-l", "--lines", "-g", "--grep", "--offset", "--full", "--column",
        "-o", "--output", "--response-format", "--svg-compression-level",
        "--encoding", "--default", "--debug-output",
    }),
    "mouse": frozenset({
        "-h", "--help",
        "--button", "--count", "--direction", "--times", "--ctrl", "--shift",
        "--alt", "--grep", "-l", "--lines",
        "-t", "--trigger", "--newline", "--timeout", "--idle-timeout",
        "--idle-after-first-output", "--keep-ansi", "-s", "--snapshot-diff",
        "--notify",
        "-o", "--output", "--response-format", "--svg-compression-level",
        "--encoding", "--default", "--debug-output",
    }),
}

_MAX_PLUGIN_ID_LEN = 64
_MAX_OPTION_NAME_LEN = 64
_MAX_OPTIONS_BYTES = 65536


def option_strings(option: PluginCliOption) -> List[str]:
    """选项串列表（长选项 + 可选短选项）"""
    strings = ["--" + option.name]
    if option.short:
        strings.append("-" + option.short)
    return strings


def check_cli_option_conflicts(manifests: List[PluginManifest]) -> Dict[str, str]:
    """全量冲突检测 → {插件 id: 错误描述}

    按命令域判定（结果对称，与清单顺序无关）：
    - 插件选项串命中该命令内置保留选项 → 冲突
    - 两插件在同一命令声明相同长选项或相同短选项 → 双方都冲突
    - 仅在双方都注册的命令上同串才算冲突（命令域隔离）
    """
    by_cmd: Dict[str, Dict[str, List[str]]] = {
        cmd: {} for cmd in CLI_OPTION_COMMANDS
    }
    for manifest in manifests:
        for option in manifest.cli_options:
            cmds = option.commands or list(CLI_OPTION_COMMANDS)
            for cmd in cmds:
                for s in option_strings(option):
                    by_cmd[cmd].setdefault(s, []).append(manifest.id)

    conflicts: Dict[str, str] = {}
    for cmd, mapping in by_cmd.items():
        for s, ids in mapping.items():
            if s in RESERVED_OPTIONS[cmd]:
                for pid in ids:
                    conflicts.setdefault(
                        pid, "选项 %s 与内置参数冲突（命令 %s）" % (s, cmd)
                    )
            elif len(ids) > 1:
                for pid in ids:
                    others = sorted(x for x in ids if x != pid)
                    conflicts.setdefault(
                        pid,
                        "选项 %s 与插件 %s 冲突（命令 %s）" % (s, others, cmd),
                    )
    return conflicts


class OptionRegistration:
    """单个插件选项的 argparse 注册描述"""

    __slots__ = ("plugin_id", "name", "strings", "kwargs")

    def __init__(self, plugin_id: str, name: str, strings: List[str], kwargs: dict):
        self.plugin_id = plugin_id
        self.name = name
        self.strings = strings
        self.kwargs = kwargs

    @property
    def dest(self) -> str:
        return self.kwargs["dest"]


def _argparse_kwargs(plugin_id: str, option: PluginCliOption) -> dict:
    """按选项声明生成 argparse 参数

    未声明 default 时 default=SUPPRESS（未显式提供不产生属性，collect 跳过）；
    声明 default 时落地为 argparse 默认值（插件总能拿到声明默认值）。
    flag 类型保持 store_true 语义，不落 default（default=True 会使选项恒真）。
    """
    kwargs = {
        "dest": "plugin_%s_%s" % (plugin_id, option.name),
        "default": argparse.SUPPRESS,
        "help": option.help or "",
    }
    if option.type == "flag":
        kwargs["action"] = "store_true"
        return kwargs
    if option.type == "int":
        kwargs["type"] = int
    elif option.type == "float":
        kwargs["type"] = float
    if option.type == "choice":
        kwargs["choices"] = list(option.choices or [])
    if option.default is not None:
        kwargs["default"] = option.default
    return kwargs


def build_option_registrations(
    manifests: List[PluginManifest],
    conflicted: Optional[Dict[str, str]] = None,
) -> Dict[str, List[OptionRegistration]]:
    """生成命令 → 注册描述列表（仅非冲突插件的选项，按清单顺序）

    conflicted: 预先计算的冲突映射（check_cli_option_conflicts 结果）；
    缺省时内部重算（独立调用场景）。
    """
    if conflicted is None:
        conflicted = check_cli_option_conflicts(manifests)
    regs: Dict[str, List[OptionRegistration]] = {}
    for manifest in manifests:
        if manifest.id in conflicted:
            continue
        for option in manifest.cli_options:
            cmds = option.commands or list(CLI_OPTION_COMMANDS)
            kwargs = _argparse_kwargs(manifest.id, option)
            for cmd in cmds:
                regs.setdefault(cmd, []).append(
                    OptionRegistration(
                        manifest.id, option.name, option_strings(option), kwargs
                    )
                )
    return regs


def collect_option_values(args, registrations: Dict[str, List[OptionRegistration]]) -> dict:
    """从 parse 结果收集显式提供的插件选项 → {插件 id: {选项名: 值}}

    default=SUPPRESS 保证未显式提供的选项属性不存在；同插件选项跨命令
    注册时 dest 相同，重复遍历只收集一次。
    """
    values: dict = {}
    for cmd_regs in registrations.values():
        for reg in cmd_regs:
            if hasattr(args, reg.dest):
                values.setdefault(reg.plugin_id, {})[reg.name] = getattr(args, reg.dest)
    return values


def validate_plugin_options(value) -> Optional[str]:
    """校验 pluginOptions 消息形状；合法返回 None，非法返回错误描述

    daemon 侧对 exec/send/read/mouse 消息统一调用，防伪造/超长。
    """
    if not isinstance(value, dict):
        return "pluginOptions 必须为对象"
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return "pluginOptions 含非法值"
    if len(raw.encode("utf-8")) > _MAX_OPTIONS_BYTES:
        return "pluginOptions 过大"
    for pid, opts in value.items():
        if not isinstance(pid, str) or not pid or len(pid) > _MAX_PLUGIN_ID_LEN:
            return "pluginOptions 插件名非法"
        if not isinstance(opts, dict):
            return "pluginOptions.%s 必须为对象" % pid
        for name, v in opts.items():
            if not isinstance(name, str) or not name or len(name) > _MAX_OPTION_NAME_LEN:
                return "pluginOptions.%s 选项名非法" % pid
            if not isinstance(v, (str, int, float, bool)):
                return "pluginOptions.%s.%s 值类型非法" % (pid, name)
    return None
