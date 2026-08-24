"""web/application/handlers.py 单元测试

测试 SessionDetailHandler 和 SessionDetailRefreshHandler。
"""

import asyncio
import time
import pytest

from src.web.application.handlers.base import HandlerContext
from src.web.application.handlers.detail import (
    SessionDetailHandler,
    SessionDetailRefreshHandler,
)
from src.web.application.handlers.system import ListSessionsHandler
from src.web.domain.entities import ActiveSession, HistoryDetail


class _MockSession:
    def __init__(self, sid="test", running=True):
        self.id = sid
        self.uid = "uid-test"
        self.running = running
        self.command = "echo test"
        self.start_time = time.time()
        self.exit_code = None
        self.error_message = None
        self.pty_type = "win-conpty"
        self._cols = 80
        self._rows = 24
        self._cwd = "/tmp"
        self.encoding = "utf-8"
        self._pty = _MockPty()
        self._out_buf = _MockBuffer()
        self.output_offset = 100
        self.gui_windows = []
        self.event_history = _MockEventHistory()

    @property
    def cols(self):
        return self._cols

    @property
    def rows(self):
        return self._rows

    @property
    def cwd(self):
        return self._cwd

    def get_all_events(self, **kwargs):
        return self.event_history.get_all(**kwargs)


class _MockPty:
    def get_process_list(self):
        return []

    def get_type(self):
        return "win-conpty"

    def get_child_pid(self):
        return None


class _MockBuffer:
    def __init__(self):
        self.length = 0

    def get_slice(self, start=0):
        return b""


class _MockEventHistory:
    def __init__(self):
        self._events = []

    def get_all(self, **kwargs):
        return list(self._events)

    def add_event_listener(self, listener):
        pass

    def remove_event_listener(self, listener):
        pass


class _MockSessionRepo:
    def __init__(self, sessions=None):
        self._sessions = sessions or {}

    def get_session(self, sid):
        return self._sessions.get(sid)

    def get_by_uid(self, uid):
        for s in self._sessions.values():
            if s.uid == uid:
                return s
        return None

    def resolve_sid(self, sid):
        s = self._sessions.get(sid)
        return s.uid if s else None

    def list_sessions(self):
        return [
            ActiveSession(
                id=s.id, uid=s.uid, command=s.command,
                running=s.running, start_time=s.start_time,
            )
            for s in self._sessions.values()
        ]

    def create_session(self, *a, **kw):
        pass

    def remove_session(self, *a, **kw):
        pass

    def set_on_session_created(self, cb):
        pass

    def set_on_session_removed(self, cb):
        pass


class _MockHistoryRepo:
    def __init__(self, details=None):
        self._details = details or {}

    def get_session_detail(self, identifier):
        # uid 优先，其次按 id(sid) 匹配
        if identifier in self._details:
            return self._details[identifier]
        for d in self._details.values():
            if isinstance(d, dict) and (
                d.get("id") == identifier or d.get("uid") == identifier
            ):
                return d
            if hasattr(d, "id") and (d.id == identifier or d.uid == identifier):
                return d
        return None

    def list_sessions(self):
        return []

    def delete_session(self, sid):
        pass


class _MockHistoryStoreWithDetail:
    def __init__(self, detail):
        self._detail = detail

    def get_session_detail(self, sid):
        if sid == self._detail.get("id"):
            return dict(self._detail)
        return None


class _MockExecutor:
    async def run(self, fn, *args, **kwargs):
        if callable(fn):
            return fn(*args, **kwargs)
        return fn


class _MockConnection:
    def __init__(self):
        self._decoders = {}
        self._callbacks = {}

    def get_decoder(self, sid):
        return self._decoders.get(sid)

    def set_decoder(self, sid, decoder):
        self._decoders[sid] = decoder

    def remove_decoder(self, sid):
        self._decoders.pop(sid, None)

    def get_callbacks(self):
        return self._callbacks

    def clear_callbacks(self):
        self._callbacks = {}


class _MockChannel:
    def __init__(self):
        self.messages = []

    async def send(self, msg):
        self.messages.append(msg)

    async def close(self, code=1000):
        pass

    @property
    def closed(self):
        return False


def _make_ctx(sessions=None, history_details=None):
    session_repo = _MockSessionRepo(sessions)
    history_repo = _MockHistoryRepo(history_details)
    connection = _MockConnection()
    channel = _MockChannel()
    executor = _MockExecutor()

    class _MockEncoder:
        pass

    class _MockSubscription:
        pass

    class _MockPublisher:
        def publish_session_created(self, *a, **kw):
            pass

        def publish_session_removed(self, *a, **kw):
            pass

    class _MockStats:
        pass

    class _MockShell:
        pass

    return HandlerContext(
        session_repo=session_repo,
        history_repo=history_repo,
        system_stats=_MockStats(),
        shell_provider=_MockShell(),
        executor=executor,
        encoder=_MockEncoder(),
        subscription=_MockSubscription(),
        publisher=_MockPublisher(),
        connection=connection,
        channel=channel,
        enqueue=lambda msg: None,
    )


class TestSessionDetailHandler:
    def test_session_not_found(self):
        async def _test():
            ctx = _make_ctx()
            handler = SessionDetailHandler()
            results = await handler.handle(ctx, {"session_id": "nonexistent"})
            assert len(results) == 1
            assert results[0]["type"] == "error"
            assert "not found" in results[0]["message"]
        asyncio.run(_test())

    def test_active_session_detail(self):
        async def _test():
            session = _MockSession("test-sess")
            ctx = _make_ctx(sessions={"test-sess": session})
            handler = SessionDetailHandler()
            results = await handler.handle(ctx, {"session_id": "test-sess"})
            assert len(results) == 1
            msg = results[0]
            assert msg["type"] == "session_detail"
            assert msg["source"] == "active"
            assert msg["id"] == "test-sess"
            assert msg["uid"] == "uid-test"
            assert msg["command"] == "echo test"
            assert msg["ptyType"] == "win-conpty"
            assert msg["encoding"] == "utf-8"
            assert msg["running"] is True
            assert "startTime" in msg
            assert isinstance(msg["startTime"], float)
            assert "processTree" in msg
            assert "processDetails" in msg
            assert "events" in msg
        asyncio.run(_test())

    def test_detail_includes_cwd(self):
        async def _test():
            session = _MockSession("test-sess")
            ctx = _make_ctx(sessions={"test-sess": session})
            handler = SessionDetailHandler()
            results = await handler.handle(ctx, {"session_id": "test-sess"})
            msg = results[0]
            assert msg["cwd"] == "/tmp"
        asyncio.run(_test())

    def test_detail_includes_terminal_size(self):
        async def _test():
            session = _MockSession("test-sess")
            ctx = _make_ctx(sessions={"test-sess": session})
            handler = SessionDetailHandler()
            results = await handler.handle(ctx, {"session_id": "test-sess"})
            msg = results[0]
            assert msg["cols"] == 80
            assert msg["rows"] == 24
        asyncio.run(_test())

    def test_detail_includes_output_size(self):
        async def _test():
            session = _MockSession("test-sess")
            ctx = _make_ctx(sessions={"test-sess": session})
            handler = SessionDetailHandler()
            results = await handler.handle(ctx, {"session_id": "test-sess"})
            msg = results[0]
            assert "outputSize" in msg
        asyncio.run(_test())

    def test_detail_with_ended_session(self):
        async def _test():
            session = _MockSession("test-sess", running=False)
            session.exit_code = 1
            session.error_message = "test error"
            ctx = _make_ctx(sessions={"test-sess": session})
            handler = SessionDetailHandler()
            results = await handler.handle(ctx, {"session_id": "test-sess"})
            msg = results[0]
            assert msg["running"] is False
            assert msg["exitCode"] == 1
            assert msg["errorMessage"] == "test error"
        asyncio.run(_test())

    def test_history_session_detail(self):
        async def _test():
            detail = HistoryDetail(
                id="hist-sess",
                command="echo hist",
                pty_type="win-conpty",
                cols=80,
                rows=24,
                encoding="utf-8",
                start_time=time.time() - 100,
                end_time=time.time(),
                exit_code=0,
                error_message=None,
            )
            ctx = _make_ctx(history_details={"hist-sess": detail})
            handler = SessionDetailHandler()
            results = await handler.handle(ctx, {"session_id": "hist-sess"})
            msg = results[0]
            assert msg["type"] == "session_detail"
            assert msg["source"] == "history"
            assert msg["id"] == "hist-sess"
        asyncio.run(_test())


class TestListSessionsHandler:
    def test_list_sessions_includes_startTime(self):
        async def _test():
            session = _MockSession("time-test")
            ctx = _make_ctx(sessions={"time-test": session})
            handler = ListSessionsHandler()
            results = await handler.handle(ctx, {})
            msg = results[0]
            assert msg["type"] == "session_list"
            assert len(msg["sessions"]) == 1
            assert "startTime" in msg["sessions"][0]
            assert isinstance(msg["sessions"][0]["startTime"], float)
        asyncio.run(_test())


class TestSessionDetailRefreshHandler:
    def test_info_tab_returns_basic_fields(self):
        async def _test():
            session = _MockSession("refresh-test")
            session.output_offset = 2048
            ctx = _make_ctx(sessions={"refresh-test": session})
            handler = SessionDetailRefreshHandler()
            results = await handler.handle(ctx, {
                "session_id": "refresh-test", "tab": "info",
            })
            msg = results[0]
            assert msg["type"] == "session_detail_refresh"
            assert msg["tab"] == "info"
            assert msg["running"] is True
            assert msg["outputSize"] == 2048
        asyncio.run(_test())

    def test_info_tab_ended_session(self):
        async def _test():
            session = _MockSession("refresh-ended", running=False)
            session.exit_code = 0
            ctx = _make_ctx(sessions={"refresh-ended": session})
            handler = SessionDetailRefreshHandler()
            results = await handler.handle(ctx, {
                "session_id": "refresh-ended", "tab": "info",
            })
            msg = results[0]
            assert msg["running"] is False
            assert msg["exitCode"] == 0
        asyncio.run(_test())

    def test_info_tab_nonexistent_session(self):
        async def _test():
            ctx = _make_ctx()
            handler = SessionDetailRefreshHandler()
            results = await handler.handle(ctx, {
                "session_id": "no-such", "tab": "info",
            })
            assert len(results) == 0
        asyncio.run(_test())

    def test_process_tab_returns_process_details(self):
        async def _test():
            session = _MockSession("proc-refresh")
            ctx = _make_ctx(sessions={"proc-refresh": session})
            handler = SessionDetailRefreshHandler()
            results = await handler.handle(ctx, {
                "session_id": "proc-refresh", "tab": "process",
            })
            msg = results[0]
            assert msg["type"] == "session_detail_refresh"
            assert msg["tab"] == "process"
            assert "processDetails" in msg
        asyncio.run(_test())

    def test_process_tab_no_tree(self):
        async def _test():
            session = _MockSession("proc-refresh")
            ctx = _make_ctx(sessions={"proc-refresh": session})
            handler = SessionDetailRefreshHandler()
            results = await handler.handle(ctx, {
                "session_id": "proc-refresh", "tab": "process",
            })
            msg = results[0]
            assert "processTree" not in msg
            assert "events" not in msg
        asyncio.run(_test())
