import socket

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler, HandlerContext
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class StopHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        Message.send(conn, Response.stop_result(0, "Daemon stopped successfully"))
        _logger.info("收到停止命令，关闭守护进程...")
        try:
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        conn.close()

        if ctx.server:
            ctx.server.stop()
