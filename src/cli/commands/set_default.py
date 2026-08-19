"""set-default 命令：覆盖默认配置（会话级）"""

import argparse
import sys

from ..base import Command, CommandContext
from ..common_args import _parse_default_key


class SetDefaultCommand(Command):
    """set-default 命令"""

    name = "set-default"
    help = "覆盖默认配置（会话级）"
    use_common_args = False
    needs_client = False

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "key",
            metavar="KEY",
            help="配置键名 (timeout/newline/keep-ansi/encoding/debug/send-eol/response-format/svg-compression-level/terminal-size)",
        )
        parser.add_argument("value", metavar="VALUE", help="配置值")

    def run(self, args, ctx: CommandContext) -> None:
        from ...client.config_manager import (
            ConfigManager,
            load_persistent_defaults,
            save_persistent_defaults,
        )
        from ...client.presenter import emit, emit_error

        cfg = ConfigManager()
        internal_key = _parse_default_key(args.key)
        try:
            cfg.set(internal_key, args.value)
        except ValueError as e:
            emit_error(str(e))
            sys.exit(1)

        persistent = load_persistent_defaults()
        persistent[internal_key] = cfg.get(internal_key)
        save_persistent_defaults(persistent)

        emit(
            f"已设置默认值: {args.key} = {cfg.get(internal_key)}"
            "（将随后续会话命令自动生效）"
        )