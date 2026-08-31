"""events handler 单元测试：ended 会话历史回放的 last/since/until 过滤

回归：R2-2 — ended 会话的 events --since/--until 过滤不生效
（ended 分支此前直接返回全量事件，未应用过滤；running 会话正常）。
"""

import socket
import threading

from src.daemon.handlers.dispatcher import DaemonDispatcher
from src.daemon.handlers.events_handler import _event_time_to_ts, _filter_event_dicts
from src.auth.context import AuthContext
from src.auth.token import TokenAuthenticator
from src.protocol.envelope import unwrap as _env_unwrap
from src.protocol.message import Message


def _read_body(cli):
    """读取一条响应并按线协议解信封"""
    resp = Message.recv(cli)
    if resp is None:
        return None
    _, body, _ = _env_unwrap(resp)
    return body


class _EndedHistoryStore:
    """模拟 history_store：仅实现 ended 事件回放；未知会话返回 None（同真实实现）"""

    def __init__(self, events, known=True):
        self._events = events
        self._known = known

    def get_ended_events(self, identifier):
        if not self._known:
            return None
        return list(self._events)


class _EndedManager:
    """模拟 manager：会话均已移除（走 history_store 分支），暴露 _history_store"""

    def __init__(self, events, known=True):
        self.history_store = _EndedHistoryStore(events, known=known)
        self._sessions = {}

    def get_session(self, sid):
        return self._sessions.get(sid)


def _ev(time_str, etype="process_spawn", pid=1):
    return {"time": time_str, "type": etype, "pid": pid}


def _ts(time_str):
    """与 _event_time_to_ts 同基准的时间戳（本地时区）"""
    return _event_time_to_ts({"time": time_str})


class TestEventTimeToTs:
    def test_parse_local_iso(self):
        # 本地时区无偏移 ISO 字符串 → 本地时间戳
        from datetime import datetime

        t = "2026-01-01T10:00:02.000000"
        expected = datetime.fromisoformat(t).timestamp()
        assert _event_time_to_ts({"time": t}) == expected

    def test_parse_seconds(self):
        # 秒级精度（无毫秒）同样可解析
        from datetime import datetime

        t = "2026-01-01T10:00:02"
        expected = datetime.fromisoformat(t).timestamp()
        assert _event_time_to_ts({"time": t}) == expected

    def test_parse_with_offset(self):
        assert _event_time_to_ts({"time": "1970-01-01T00:00:02.000000+00:00"}) == 2.0

    def test_missing_or_invalid(self):
        assert _event_time_to_ts({}) is None
        assert _event_time_to_ts({"time": None}) is None
        assert _event_time_to_ts({"time": "not-a-date"}) is None


class TestFilterEventDicts:
    def test_no_filter_returns_all(self):
        events = [_ev("2026-01-01T10:00:00.000000"), _ev("2026-01-01T11:00:00.000000")]
        assert _filter_event_dicts(events) == events

    def test_since(self):
        events = [
            _ev("2026-01-01T10:00:00.000000"),
            _ev("2026-01-01T11:00:00.000000"),
        ]
        result = _filter_event_dicts(events, since=_ts("2026-01-01T10:30:00.000000"))
        assert [e["time"] for e in result] == ["2026-01-01T11:00:00.000000"]

    def test_since_boundary_inclusive(self):
        events = [_ev("2026-01-01T10:00:00.000000")]
        assert _filter_event_dicts(events, since=_ts(events[0]["time"])) == events

    def test_until(self):
        events = [
            _ev("2026-01-01T10:00:00.000000"),
            _ev("2026-01-01T11:00:00.000000"),
        ]
        result = _filter_event_dicts(events, until=_ts("2026-01-01T10:30:00.000000"))
        assert [e["time"] for e in result] == ["2026-01-01T10:00:00.000000"]

    def test_until_boundary_inclusive(self):
        events = [_ev("2026-01-01T10:00:00.000000")]
        assert _filter_event_dicts(events, until=_ts(events[0]["time"])) == events

    def test_since_until_combined(self):
        events = [
            _ev("2026-01-01T10:00:00.000000"),
            _ev("2026-01-01T11:00:00.000000"),
            _ev("2026-01-01T12:00:00.000000"),
        ]
        result = _filter_event_dicts(
            events,
            since=_ts("2026-01-01T10:30:00.000000"),
            until=_ts("2026-01-01T11:30:00.000000"),
        )
        assert [e["time"] for e in result] == ["2026-01-01T11:00:00.000000"]

    def test_last(self):
        events = [
            _ev("2026-01-01T10:00:00.000000"),
            _ev("2026-01-01T11:00:00.000000"),
            _ev("2026-01-01T12:00:00.000000"),
        ]
        result = _filter_event_dicts(events, last=2)
        assert [e["time"] for e in result] == [
            "2026-01-01T11:00:00.000000",
            "2026-01-01T12:00:00.000000",
        ]

    def test_last_zero_or_none(self):
        events = [_ev("2026-01-01T10:00:00.000000"), _ev("2026-01-01T11:00:00.000000")]
        assert _filter_event_dicts(events, last=0) == events
        assert _filter_event_dicts(events, last=None) == events

    def test_since_plus_last(self):
        events = [
            _ev("2026-01-01T10:00:00.000000"),
            _ev("2026-01-01T11:00:00.000000"),
            _ev("2026-01-01T12:00:00.000000"),
        ]
        result = _filter_event_dicts(
            events, since=_ts("2026-01-01T10:30:00.000000"), last=1
        )
        assert [e["time"] for e in result] == ["2026-01-01T12:00:00.000000"]

    def test_unparsable_time_kept(self):
        # time 解析失败的事件保留（不因过滤丢事件）
        events = [_ev("bad-time"), _ev("2026-01-01T11:00:00.000000")]
        result = _filter_event_dicts(events, since=_ts("2026-01-01T12:00:00.000000"))
        assert [e["time"] for e in result] == ["bad-time"]


class TestEndedEventsHandler:
    """ended 会话（session 已从 manager 移除）经 history_store 回放的过滤"""

    def _handle_events(self, handler, msg_dict):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        ready = threading.Event()

        def server():
            ready.set()
            conn, _ = srv.accept()
            try:
                handler.handle(conn, ("127.0.0.1", 0))
            finally:
                conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()
        ready.wait(timeout=5)

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        msg_dict.setdefault("auth", {})["token"] = "test-token"
        Message.send(cli, msg_dict)
        resp = _read_body(cli)
        cli.close()
        srv.close()
        t.join(timeout=5)
        return resp

    def _mk_handler(self, events, known=True):
        mgr = _EndedManager(events, known=known)
        handler = DaemonDispatcher(
            mgr, AuthContext(authenticator=TokenAuthenticator("test-token"))
        )
        return handler

    def test_no_filter_returns_all(self):
        events = [
            _ev("2026-01-01T10:00:00.000000", "process_spawn", 1),
            _ev("2026-01-01T11:00:00.000000", "process_exit", 1),
        ]
        handler = self._mk_handler(events)
        resp = self._handle_events(handler, {"type": "events", "id": "ended-sess"})
        assert resp is not None
        assert resp["commandType"] == "events"
        assert resp["count"] == 2
        assert [e["time"] for e in resp["pendingEvents"]] == [
            "2026-01-01T10:00:00.000000",
            "2026-01-01T11:00:00.000000",
        ]

    def test_since_filters(self):
        events = [
            _ev("2026-01-01T10:00:00.000000", "process_spawn", 1),
            _ev("2026-01-01T11:00:00.000000", "process_exit", 1),
        ]
        handler = self._mk_handler(events)
        resp = self._handle_events(
            handler,
            {
                "type": "events",
                "id": "ended-sess",
                "since": _ts("2026-01-01T10:30:00.000000"),
            },
        )
        assert resp is not None
        assert resp["count"] == 1
        assert [e["time"] for e in resp["pendingEvents"]] == [
            "2026-01-01T11:00:00.000000"
        ]

    def test_until_filters(self):
        events = [
            _ev("2026-01-01T10:00:00.000000", "process_spawn", 1),
            _ev("2026-01-01T11:00:00.000000", "process_exit", 1),
        ]
        handler = self._mk_handler(events)
        resp = self._handle_events(
            handler,
            {
                "type": "events",
                "id": "ended-sess",
                "until": _ts("2026-01-01T10:30:00.000000"),
            },
        )
        assert resp is not None
        assert resp["count"] == 1
        assert [e["time"] for e in resp["pendingEvents"]] == [
            "2026-01-01T10:00:00.000000"
        ]

    def test_since_future_returns_empty(self):
        events = [
            _ev("2026-01-01T10:00:00.000000", "process_spawn", 1),
            _ev("2026-01-01T11:00:00.000000", "process_exit", 1),
        ]
        handler = self._mk_handler(events)
        resp = self._handle_events(
            handler,
            {
                "type": "events",
                "id": "ended-sess",
                "since": _ts("2026-01-01T12:00:00.000000"),
            },
        )
        assert resp is not None
        assert resp["count"] == 0
        assert resp["pendingEvents"] == []

    def test_last_filters(self):
        events = [
            _ev("2026-01-01T10:00:00.000000", "process_spawn", 1),
            _ev("2026-01-01T11:00:00.000000", "process_exit", 1),
        ]
        handler = self._mk_handler(events)
        resp = self._handle_events(
            handler, {"type": "events", "id": "ended-sess", "last": 1}
        )
        assert resp is not None
        assert resp["count"] == 1
        assert [e["time"] for e in resp["pendingEvents"]] == [
            "2026-01-01T11:00:00.000000"
        ]

    def test_since_and_last_combined(self):
        events = [
            _ev("2026-01-01T10:00:00.000000", "process_spawn", 1),
            _ev("2026-01-01T11:00:00.000000", "process_exit", 1),
        ]
        handler = self._mk_handler(events)
        resp = self._handle_events(
            handler,
            {
                "type": "events",
                "id": "ended-sess",
                "since": _ts("2026-01-01T10:30:00.000000"),
                "last": 1,
            },
        )
        assert resp is not None
        assert resp["count"] == 1
        assert [e["time"] for e in resp["pendingEvents"]] == [
            "2026-01-01T11:00:00.000000"
        ]

    def test_ended_hint_present(self):
        events = [_ev("2026-01-01T10:00:00.000000", "process_spawn", 1)]
        handler = self._mk_handler(events)
        resp = self._handle_events(handler, {"type": "events", "id": "ended-sess"})
        assert resp is not None
        assert "ended" in (resp.get("hint") or "")

    def test_not_found(self):
        handler = self._mk_handler([], known=False)
        resp = self._handle_events(handler, {"type": "events", "id": "no-such"})
        assert resp is not None
        assert resp["type"] == "error"
        assert "not found" in resp["message"].lower()
