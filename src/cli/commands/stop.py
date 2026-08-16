"""stop 命令：停止后台守护进程"""

import argparse

from ..base import Command, CommandContext


class StopCommand(Command):
    """stop 命令"""

    name = "stop"
    help = "停止后台守护进程"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--force",
            "-f",
            action="store_true",
            help="强制清理（端口丢失时通过互斥锁定位并终止守护进程）",
        )

    def run(self, args, ctx: CommandContext) -> None:
        ctx.client.cmd_stop(force=getattr(args, "force", False))
