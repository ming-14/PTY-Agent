"""plugin 命令：插件管理（list/ls/attach/detach/cmd）"""

import argparse

from ..base import Command, CommandContext
from ..common_args import add_common_args


class PluginCommand(Command):
    """plugin 命令"""

    name = "plugin"
    help = "插件管理（list/ls/attach/detach/cmd）"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        plugin_sub = parser.add_subparsers(dest="plugin_subcmd", help="插件子命令")

        p_list = plugin_sub.add_parser("list", help="列出已加载插件")
        add_common_args(p_list)

        p_ls = plugin_sub.add_parser("ls", help="列出会话挂载的插件")
        add_common_args(p_ls)
        p_ls.add_argument("id", help="会话标识")

        p_attach = plugin_sub.add_parser("attach", help="动态挂载插件到运行中的会话")
        add_common_args(p_attach)
        p_attach.add_argument("id", help="会话标识")
        p_attach.add_argument("name", help="插件名")

        p_detach = plugin_sub.add_parser("detach", help="从会话卸载插件")
        add_common_args(p_detach)
        p_detach.add_argument("id", help="会话标识")
        p_detach.add_argument("name", help="插件名")

        p_cmd = plugin_sub.add_parser("cmd", help="调用插件自定义命令")
        add_common_args(p_cmd)
        p_cmd.add_argument("id", help="会话标识")
        p_cmd.add_argument("name", help="插件名")
        p_cmd.add_argument("command", help="命令名")
        p_cmd.add_argument("args", nargs="*", default=None, help="命令参数（可选）")

    def run(self, args, ctx: CommandContext) -> None:
        if args.plugin_subcmd == "list":
            ctx.client.cmd_plugin("list")
        elif args.plugin_subcmd == "ls":
            ctx.client.cmd_plugin("ls", session_id=args.id)
        elif args.plugin_subcmd == "attach":
            ctx.client.cmd_plugin("attach", session_id=args.id, name=args.name)
        elif args.plugin_subcmd == "detach":
            ctx.client.cmd_plugin("detach", session_id=args.id, name=args.name)
        elif args.plugin_subcmd == "cmd":
            ctx.client.cmd_plugin(
                "cmd",
                session_id=args.id,
                name=args.name,
                command=args.command,
                args=args.args,
            )