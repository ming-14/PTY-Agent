import socket

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler
from ...execution.context import HandlerContext
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class KillHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        session_id = msg.get("id", "")
        _logger.info("_handle_kill: id=%r", session_id)
        if not session_id:
            Message.send(conn, Response.error("Missing session id"))
            return
        session = ctx.manager.get_session(session_id)
        if not session:
            hs = ctx.manager.history_store
            if hs and hs.get_session_tag(session_id) == "ended":
                hs.delete_session(session_id)
                Message.send(conn, Response.kill_result(0, "Ended session removed"))
                return
            Message.send(
                conn, Response.kill_result(-1, f"Session '{session_id}' not found")
            )
            return

        # 会话历史归档由 remove_session 内部统一执行（stop 之后，exit_code 已就绪），
        # 此处不再显式归档，避免同一会话归档两次
        # 先终止会话（实际 kill 进程树），成功后才向客户端报告，
        # 避免 remove_session 失败时客户端已收到"成功"造成假成功
        try:
            ctx.manager.remove_session(session_id)
            _logger.info("会话 '%s' 已终止", session_id)
            Message.send(conn, Response.kill_result(0, "Process killed successfully"))
        except Exception as e:
            _logger.warning("终止会话 '%s' 时发生异常", session_id, exc_info=True)
            try:
                Message.send(
                    conn,
                    Response.kill_result(
                        -1, f"Failed to kill session '{session_id}': {e}"
                    ),
                )
            except Exception:
                _logger.warning("发送 kill 失败响应时发生异常", exc_info=True)
        finally:
            try:
                conn.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            conn.close()
