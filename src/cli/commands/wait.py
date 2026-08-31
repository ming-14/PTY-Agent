"""wait 命令：等待通知或指定秒数（守护进程侧等待）"""

import argparse

from ..base import Command, CommandContext


class WaitCommand(Command):
    """wait 命令"""

    name = "wait"
    help = "等待：有待消费通知立即返回摘要，否则等待指定秒数（通知到达即唤醒）"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--timeout",
            type=float,
            default=None,
            help="等待秒数（默认 120，可通过 --default timeout 修改）",
        )

    def run(self, args, ctx: CommandContext) -> None:
        ctx.client.cmd_wait(timeout=args.timeout)
