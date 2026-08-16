"""status 命令：查看守护进程运行状态"""

from ..base import Command, CommandContext


class StatusCommand(Command):
    """status 命令"""

    name = "status"
    help = "查看守护进程运行状态"

    def run(self, args, ctx: CommandContext) -> None:
        ctx.client.cmd_status()
