"""attend 命令：接管会话为完整实时终端"""

import argparse
import sys

from ..base import Command, CommandContext


class AttendCommand(Command):
    """attend 命令：把当前终端接管为会话的完整实时终端

    进入后镜像显示会话屏幕（原始字节透传，与直接运行一致），
    支持键盘/鼠标/resize 接管；不影响 web 端与其他 CLI 读。
    Ctrl+\\ 分离，Ctrl+C 透传给会话。
    """

    name = "attend"
    help = "接管会话为完整实时终端"
    description = "接管会话为完整实时终端：镜像 + 输入/鼠标/resize，不影响 web 端"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("id", help="会话标识")

    def run(self, args, ctx: CommandContext) -> None:
        sys.exit(ctx.client.cmd_attend(args.id))
