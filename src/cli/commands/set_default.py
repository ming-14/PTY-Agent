"""set-default 命令：覆盖全局默认配置（守护进程内存记忆）

默认配置存于**守护进程内存**（不写任何文件，daemon 重启即清空），
影响之后新建的会话；--default/显式参数按优先级覆盖。
"""

import argparse
import sys

from ..base import Command, CommandContext
from ..common_args import _parse_default_key
from ...config.default_keys import normalize_default_value


class SetDefaultCommand(Command):
    """set-default 命令"""

    name = "set-default"
    help = "覆盖默认配置（守护进程内存，daemon 重启即清空）"
    use_common_args = False
    needs_client = True

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "key",
            metavar="KEY",
            help="配置键名 (timeout/newline/keep-ansi/encoding/debug/send-eol/response-format/svg-compression-level/terminal-size/shell)",
        )
        parser.add_argument("value", metavar="VALUE", help="配置值")

    def run(self, args, ctx: CommandContext) -> None:
        from ...client.presenter import emit, emit_error

        internal_key = _parse_default_key(args.key)
        # 客户端先行校验（与 daemon 同一规则），尽早报错
        try:
            value = normalize_default_value(internal_key, args.value)
        except ValueError as e:
            emit_error(str(e))
            sys.exit(1)

        resp = ctx.client.cmd_set_default(internal_key, value)
        if resp.get("type") == "error":
            emit_error(resp.get("message", "设置默认配置失败"))
            sys.exit(1)
        defaults = resp.get("defaults") or {}
        emit(
            f"已设置默认值: {args.key} = {resp.get('value')}"
            "（守护进程内存记忆，daemon 重启后失效；影响之后新建的会话）"
        )
        if defaults:
            from ...client.config_manager import _format_value

            lines = []
            for k in sorted(defaults.keys()):
                lines.append(f"  {k} = {_format_value(defaults[k])}")
            emit("当前全部默认配置（守护进程内存）:\n" + "\n".join(lines))
