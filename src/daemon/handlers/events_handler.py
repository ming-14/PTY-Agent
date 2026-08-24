
from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler
from ...execution.context import HandlerContext
from ...execution.response import _EVENTS_NO_ARGS_HINT, _SESSION_ENDED_HINT
from ...logging import get_logger

_logger = get_logger("pty-daemon")


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
                    resp = Response.events_result(
                        session_id, ended_events, len(ended_events), _SESSION_ENDED_HINT
                    )
                    Message.send(conn, resp)
                    return
            Message.send(conn, Response.error(f"Session '{session_id}' not found"))
            return

        last_n = msg.get("last")
        since = msg.get("since")
        until = msg.get("until")
        has_filter = last_n is not None or since is not None or until is not None

        _logger.info(
            "_handle_events: id=%r last=%s since=%s until=%s pending=%d history=%d",
            session_id,
            last_n,
            since,
            until,
            session.pending_event_count,
            session.event_history.history_count,
        )

        if has_filter:
            events = session.get_all_events(last=last_n, since=since, until=until)
        else:
            events = session.peek_events()

        # 批量存在性判定：所有 process_spawn 事件共用一次进程列表查询
        # （原实现逐事件 get_process_list()，N 个事件 N 次全量进程扫描）
        alive_pids = None
        for ev in events:
            if ev.get("type") == "process_spawn":
                if alive_pids is None:
                    alive_pids = set(session.get_pty_process_list())
                ev["currentlyActive"] = ev.get("pid", 0) in alive_pids
            else:
                ev["currentlyActive"] = session.check_event_existence(ev)
            ev.pop("still_active", None)

        hint = ""
        # 不再输出 "Events are consumed..." 提示（噪声）：事件消费语义由 events
        # 表格本身体现即可；仅在无过滤时提示用 -l 查看完整历史
        if not has_filter:
            hint = _EVENTS_NO_ARGS_HINT
        if not session.running:
            hint = (
                (_SESSION_ENDED_HINT + " " + hint).strip()
                if hint
                else _SESSION_ENDED_HINT
            )

        resp = Response.events_result(session_id, events, len(events), hint)
        Message.send(conn, resp)
