import logging

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler, HandlerContext
from .utils import _EVENTS_HINT, _EVENTS_NO_ARGS_HINT, _SESSION_ENDED_HINT

_logger = logging.getLogger("pty-daemon")


class EventsHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        session_id = msg.get("id", "")

        if not session_id:
            Message.send(conn, Response.error("Missing session id"))
            return
        session = ctx.manager.get_session(session_id)
        if not session:
            hs = ctx.manager._history_store
            if hs:
                ended_events = hs.get_ended_events(session_id)
                if ended_events is not None:
                    resp = Response.events_result(session_id, ended_events, len(ended_events), _SESSION_ENDED_HINT)
                    Message.send(conn, resp)
                    return
            Message.send(conn, Response.error(f"Session '{session_id}' not found"))
            return

        last_n = msg.get("last")
        since = msg.get("since")
        until = msg.get("until")
        has_filter = last_n is not None or since is not None or until is not None

        _logger.info("_handle_events: id=%r last=%s since=%s until=%s "
                     "pending=%d history=%d",
                     session_id, last_n, since, until,
                     session.pending_event_count,
                     session.event_history.history_count)

        if has_filter:
            events = session.get_all_events(last=last_n, since=since, until=until)
        else:
            events = session.peek_events()

        for ev in events:
            ev["currentlyActive"] = session.check_event_existence(ev)
            ev.pop("still_active", None)

        hint = ""
        if events:
            hint = _EVENTS_HINT
        if not has_filter:
            hint = (hint + " " + _EVENTS_NO_ARGS_HINT).strip() if hint else _EVENTS_NO_ARGS_HINT
        if not session.running:
            hint = (_SESSION_ENDED_HINT + " " + hint).strip() if hint else _SESSION_ENDED_HINT

        resp = Response.events_result(session_id, events, len(events), hint)
        Message.send(conn, resp)
