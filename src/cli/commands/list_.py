"""list 命令：列出所有活跃会话"""

from ..base import Command, CommandContext


class ListCommand(Command):
    """list 命令"""

    name = "list"
    help = "列出所有活跃会话"

    def run(self, args, ctx: CommandContext) -> None:
        ctx.client.cmd_list()
