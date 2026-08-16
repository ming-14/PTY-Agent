"""wait 命令：恒等待指定秒数（守护进程侧等待）"""

import argparse

from ..base import Command, CommandContext


class WaitCommand(Command):
    """wait 命令"""

    name = "wait"
    help = "恒等待指定秒数（守护进程侧等待）"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--timeout",
            type=float,
            default=None,
            help="等待秒数（默认 120，可通过 --default timeout 修改）",
        )

    def run(self, args, ctx: CommandContext) -> None:
        ctx.client.cmd_wait(timeout=args.timeout)
