import logging

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler, HandlerContext

_logger = logging.getLogger("pty-daemon")


class ListHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        sessions = ctx.manager.list_sessions()
        for s in sessions:
            sid = s.get("id", "")
            session = ctx.manager.get_session(sid)
            s["pendingEvents"] = session.pending_event_count if session else 0
            s.pop("pending_events", None)
            if "command" in s:
                s["rawStartCommand"] = s.pop("command")
            if session and session.uid:
                s["uid"] = session.uid
        hint = "" if sessions else "No active session."
        hs = ctx.manager._history_store
        if hs:
            ended = hs.list_ended_sessions()
            for s in ended:
                s["pendingEvents"] = 0
                if "command" in s:
                    s["rawStartCommand"] = s.pop("command")
            sessions.extend(ended)
        Message.send(conn, Response.list_result(sessions, hint))
