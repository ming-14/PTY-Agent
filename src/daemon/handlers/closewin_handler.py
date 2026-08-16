
from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler, HandlerContext
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class CloseWinHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        session_id = msg.get("id", "")
        hwnd = msg.get("hwnd")

        if not session_id:
            Message.send(conn, Response.error("Missing session id"))
            return
        if hwnd is None:
            Message.send(conn, Response.error("Missing hwnd parameter"))
            return

        session = ctx.manager.get_session(session_id)
        if not session:
            Message.send(conn, Response.error(f"Session '{session_id}' not found"))
            return

        tracked_hwnds = {w["hwnd"] for w in session.gui_windows}
        if hwnd not in tracked_hwnds:
            _logger.warning(
                "closewin: hwnd=0x%X 不属于会话 '%s' 的已跟踪 GUI 窗口",
                hwnd,
                session_id,
            )
            Message.send(
                conn,
                Response.closewin_result(
                    closed=False,
                    hwnd=hwnd,
                    message=f"hwnd 0x{hwnd:X} is not a tracked GUI window in session '{session_id}'",
                ),
            )
            return

        try:
            ok = session.close_window(hwnd)
            Message.send(conn, Response.closewin_result(closed=ok, hwnd=hwnd))
        except Exception as e:
            _logger.warning("关闭窗口异常: %s", e)
            Message.send(conn, Response.error("Failed to close window"))
