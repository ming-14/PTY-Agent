"""start 命令：启动后台守护进程"""

from ..base import Command, CommandContext


class StartCommand(Command):
    """start 命令"""

    name = "start"
    help = "启动后台守护进程"

    def run(self, args, ctx: CommandContext) -> None:
        ctx.client.cmd_start()
