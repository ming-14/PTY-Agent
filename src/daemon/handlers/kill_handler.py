import logging
import socket

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler, HandlerContext

_logger = logging.getLogger("pty-daemon")


class KillHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        session_id = msg.get("id", "")
        _logger.info("_handle_kill: id=%r", session_id)
        if not session_id:
            Message.send(conn, Response.error("Missing session id"))
            return
        session = ctx.manager.get_session(session_id)
        if not session:
            hs = ctx.manager._history_store
            if hs and hs.get_session_tag(session_id) == "ended":
                hs.delete_session(session_id)
                Message.send(conn, Response.kill_result(0, "Ended session removed"))
                return
            Message.send(
                conn, Response.kill_result(-1, f"Session '{session_id}' not found")
            )
            return

        if ctx.manager._history_store:
            try:
                ctx.manager._history_store.archive_session(session, tag="history")
            except Exception as e:
                _logger.warning("kill前持久化会话 '%s' 时异常: %s", session_id, e)

        Message.send(conn, Response.kill_result(0, "Process killed successfully"))
        try:
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        conn.close()
        conn = None
        try:
            ctx.manager.remove_session(session_id)
            _logger.info("会话 '%s' 已终止", session_id)
        except Exception:
            _logger.warning("终止会话 '%s' 时发生异常", session_id, exc_info=True)
