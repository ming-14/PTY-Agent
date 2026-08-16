"""set-default 命令：覆盖默认配置（会话级）"""

import argparse
import json
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
        from ...client.input import safe_print
        from ...protocol.response import Response

        cfg = ConfigManager()
        internal_key = _parse_default_key(args.key)
        try:
            cfg.set(internal_key, args.value)
        except ValueError as e:
            safe_print(json.dumps(Response.error(str(e)), ensure_ascii=False))
            sys.exit(1)

        persistent = load_persistent_defaults()
        persistent[internal_key] = cfg.get(internal_key)
        save_persistent_defaults(persistent)

        safe_print(
            json.dumps(
                Response.info(
                    f"已设置默认值: {args.key} = {cfg.get(internal_key)}"
                    "（将随后续会话命令自动生效）"
                ),
                ensure_ascii=False,
                default=str,
            )
        )