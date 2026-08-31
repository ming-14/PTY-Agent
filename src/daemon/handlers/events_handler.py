from datetime import datetime
from typing import Optional

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler
from ...execution.context import HandlerContext
from ...execution.response import _EVENTS_NO_ARGS_HINT, _SESSION_ENDED_HINT
from ...logging import get_logger

_logger = get_logger("pty-daemon")


def _event_time_to_ts(ev: dict) -> Optional[float]:
    """事件 dict 的 time 字段（本地时区 ISO 字符串）转时间戳；失败返回 None"""
    t = ev.get("time")
    if not t:
        return None
    try:
        return datetime.fromisoformat(t).timestamp()
    except (ValueError, TypeError):
        return None


def _filter_event_dicts(
    events: list,
    last: Optional[int] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
) -> list:
    """对事件 dict 列表应用 last/since/until 过滤（ended 会话历史回放用）

    语义与 running 会话的 EventHistoryManager.get_all 对齐：since/until 为
    时间戳（含边界），last 取末尾 N 条；time 解析失败的事件保留（不因过滤丢事件）。
    """
    if since is not None or until is not None:
        filtered = []
        for ev in events:
            ts = _event_time_to_ts(ev)
            if ts is None:
                filtered.append(ev)
                continue
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue
            filtered.append(ev)
        events = filtered
    if last is not None and last > 0:
        events = events[-last:]
    return events


class EventsHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        session_id = msg.get("id", "")

        if not session_id:
            Message.send(conn, Response.error("Missing session id"))
            return

        last_n = msg.get("last")
        since = msg.get("since")
        until = msg.get("until")
        has_filter = last_n is not None or since is not None or until is not None

        session = ctx.manager.get_session(session_id)
        if not session:
            hs = ctx.manager.history_store
            if hs:
                ended_events = hs.get_ended_events(session_id)
                if ended_events is not None:
                    # ended 会话历史回放同样应用过滤，与 running 会话语义一致
                    filtered = _filter_event_dicts(
                        ended_events, last=last_n, since=since, until=until
                    )
                    _logger.info(
                        "_handle_events ended replay: id=%r raw=%d filtered=%d",
                        session_id,
                        len(ended_events),
                        len(filtered),
                    )
                    resp = Response.events_result(
                        session_id, filtered, len(filtered), _SESSION_ENDED_HINT
                    )
                    Message.send(conn, resp)
                    return
            Message.send(conn, Response.error(f"Session '{session_id}' not found"))
            return

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
