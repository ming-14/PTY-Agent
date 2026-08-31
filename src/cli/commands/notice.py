"""notice 命令：按 nid 查看通知的完整内容"""

import argparse

from ..base import Command, CommandContext


class NoticeCommand(Command):
    """notice 命令"""

    name = "notice"
    help = "查看通知的完整内容"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("nid", help="通知标识（wait 命令返回）")

    def run(self, args, ctx: CommandContext) -> None:
        ctx.client.cmd_notice(nid=args.nid)