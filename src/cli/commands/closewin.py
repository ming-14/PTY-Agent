"""closewin 命令：关闭指定 GUI 窗口"""

import argparse

from ..base import Command, CommandContext


class ClosewinCommand(Command):
    """closewin 命令"""

    name = "closewin"
    help = "关闭指定 GUI 窗口"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("id", help="会话标识")
        parser.add_argument(
            "hwnd", type=lambda x: int(x, 0), help="窗口句柄（十进制或 0x 十六进制）"
        )

    def run(self, args, ctx: CommandContext) -> None:
        ctx.client.cmd_closewin(args.id, args.hwnd)
