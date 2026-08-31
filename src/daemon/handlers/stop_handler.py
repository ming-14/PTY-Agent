import socket

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler
from ...execution.context import HandlerContext
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class StopHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        # 生存模式：拒绝 stop 协议消息（仅 SIGKILL 可终止，stop --force 可用）。
        # 返回错误响应，让客户端感知停止被拒并走 force（SIGKILL）路径。
        if getattr(getattr(ctx, "server", None), "survive", False):
            _logger.warning("生存模式：拒绝 stop 协议消息（仅 SIGKILL 可终止）")
            Message.send(conn, Response.error("survive 模式已拒绝 stop 命令，请使用 stop --force"))
            return
        Message.send(conn, Response.stop_result(0, "Daemon stopped successfully"))
        _logger.info("收到停止命令，关闭守护进程...")
        try:
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        conn.close()

        if ctx.server:
            ctx.server.stop()
