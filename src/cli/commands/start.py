"""start 命令：启动后台守护进程"""

import json

from ...client.input import safe_print
from ...protocol.response import Response
from ..base import Command, CommandContext


class StartCommand(Command):
    """start 命令"""

    name = "start"
    help = "启动后台守护进程"

    def run(self, args, ctx: CommandContext) -> None:
        # --encoding 的取值校验已在 argparse 解析期完成（非法值直接报错退出，
        # 与守护进程是否已运行无关）。该参数仅作为本次调用的取值白名单校验，
        # 并无传播路径：不参与守护进程启动，也不影响已运行守护进程的会话默认
        # 编码（会话默认编码请用 --default encoding / set-default）。
        encoding = getattr(args, "encoding", None)
        if encoding:
            safe_print(
                json.dumps(
                    Response.info(
                        f"--encoding {encoding!r} 取值已校验，但不影响守护进程启动"
                        "（会话默认编码请使用 --default encoding / set-default）"
                    ),
                    ensure_ascii=False,
                )
            )
        ctx.client.cmd_start()
