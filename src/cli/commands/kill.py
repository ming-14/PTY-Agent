"""kill 命令：终止指定会话"""

import argparse

from ..base import Command, CommandContext


class KillCommand(Command):
    """kill 命令"""

    name = "kill"
    help = "终止指定会话"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("id", help="会话标识")

    def run(self, args, ctx: CommandContext) -> None:
        ctx.client.cmd_kill(args.id)
