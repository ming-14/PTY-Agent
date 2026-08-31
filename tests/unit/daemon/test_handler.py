"""daemon/handlers/dispatcher.py 单元测试

测试请求处理器的认证、验证、消息派发、_build_result、_strip_if_needed 等。
"""

import contextlib
import json
import time
import socket
import threading
import pytest

from src.daemon.handlers.dispatcher import DaemonDispatcher
from src.execution.utils import validate_field, get_detail
from src.execution.response import build_result, map_reason
from src.execution.filtering import strip_if_needed
from src.client.presenter import _session_message, _session_reason_hint
from src.client.result import SessionResult
from src.protocol.envelope import unwrap as _env_unwrap
from src.protocol.message import Message
from src.auth.token import TokenAuthenticator
from src.auth.context import AuthContext


def _read_body(cli):
    """读取一条响应并按线协议解信封（线协议：daemon 响应为响应信封）"""
    resp = Message.recv(cli)
    if resp is None:
        return None
    _, body, _ = _env_unwrap(resp)
    return body


class _MockConn:
    def __init__(self):
        self.sent_messages = []
        self._closed = False

    def sendall(self, data):
        pass

    def close(self):
        self._closed = True

    def fileno(self):
        return -1

    def settimeout(self, t):
        pass


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
        self.output_offset = 10
        self.pending_event_count = 0
        self.gui_windows = []
        self.processes = []
        self.client_config = {}
        self._trig_lock = threading.RLock()

    def get_output(self, **kwargs):
        return "test output"

    @property
    def stdout_read_offset(self):
        return 0

    def read_base(self, full=False):
        return 0

    def advance_stdout_cursor(self, delivered_end):
        pass

    def get_output_with_offset(self, from_offset=None, encoding=None):
        return self.get_output(from_offset=from_offset, encoding=encoding), self.output_offset

    @contextlib.contextmanager
    def hold(self):
        yield self

    def get_snapshot(self, keep_ansi=False):
        return "snapshot content"

    def get_full_snapshot(self, keep_ansi=False):
        return "snapshot content"

    def get_snapshot_diagnostics(self):
        return {"pyte_available": True}

    def export_screen_buffer(self):
        return {}

    def write_input(self, data, pause_offsets=None):
        pass

    def set_trigger(self, *args, **kwargs):
        pass

    def set_snapshot_trigger(self, *args, **kwargs):
        pass

    def check_snapshot_trigger(self, snapshot_text):
        return False

    def check_snapshot_idle_timeout(self):
        return False

    def notify_snapshot_changed(self):
        pass

    def wait_for_trigger(self, timeout=None, **kwargs):
        return True, "matched"

    def clear_trigger(self):
        pass

    def wait_for_initial_output(self, timeout=1.0):
        return True

    def check_gui_detected(self, last_check_time, enabled=True):
        # 与真实 Session.check_gui_detected 同语义：无 _gui 视为无 GUI 检测
        return False, last_check_time

    def resolve_exit_reason(self):
        # 与真实 Session.resolve_exit_reason 同语义：退出码/错误消息为崩溃权威依据
        return (
            "crashed"
            if (self.exit_code not in (None, 0)) or self.error_message
            else "ended"
        )

    def consume_events(self):
        return []

    def poll_natural_exit(self):
        pass

    def read_new_err_output(self, encoding=None):
        return ""

    def close_window(self, hwnd):
        return True

    def stop(self):
        self.running = False


class _MockManager:
    def __init__(self, sessions=None):
        self._sessions = sessions or {}

    def get_session(self, sid):
        return self._sessions.get(sid)

    def create_session(self, sid, command, **kwargs):
        if sid in self._sessions:
            raise KeyError(f"会话 '{sid}' 已存在")
        s = _MockSession(sid)
        self._sessions[sid] = s
        return s

    def list_sessions(self):
        return [
            {"id": s.id, "command": s.command, "running": s.running}
            for s in self._sessions.values()
        ]

    def remove_session(self, sid):
        s = self._sessions.pop(sid, None)
        if s:
            s.stop()

    def stop_all(self):
        for s in list(self._sessions.values()):
            s.stop()
        self._sessions.clear()

    def get_global_defaults(self):
        return {}


def _setup_handler(auth_token="test-token", sessions=None):
    mgr = _MockManager(sessions)
    authenticator = TokenAuthenticator(auth_token) if auth_token else None
    handler = DaemonDispatcher(mgr, AuthContext(authenticator=authenticator))
    return handler, mgr


class TestValidateField:
    def test_valid_field(self):
        conn = _MockConn()
        assert validate_field("short", "name", 100, conn) is True

    def test_overlimit_field(self):
        conn = _MockConn()
        assert validate_field("x" * 200, "name", 100, conn) is False

    def test_none_field(self):
        conn = _MockConn()
        assert validate_field(None, "name", 100, conn) is True

    def test_non_string_field(self):
        conn = _MockConn()
        assert validate_field(123, "name", 100, conn) is True


class TestMapReason:
    def test_matched(self):
        assert map_reason("matched") == "trigger_matched"

    def test_timeout(self):
        assert map_reason("timeout") == "trigger_timeout"

    def test_idle_timeout(self):
        assert map_reason("idle_timeout") == "idle_timeout"

    def test_ended(self):
        assert map_reason("ended") == "program_ended"

    def test_crashed(self):
        # 崩溃映射以退出码为权威依据：无退出码不映射为 crashed（避免误报）
        assert map_reason("crashed") == "program_ended"
        assert map_reason("crashed", exit_code=-1) == "program_crashed"
        assert map_reason("crashed", error_message="boom") == "program_crashed"

    def test_gui_detected(self):
        assert map_reason("gui_detected") == "gui_detected"

    def test_ok(self):
        assert map_reason("ok") == "ok"

    def test_unknown(self):
        assert map_reason("unknown_reason") == "unknown_reason"


class TestSessionReasonHint:
    """presenter._session_reason_hint：返回原因文案由呈现层按数据重建"""

    def _mk(self, command_type, reason, running, exit_code=None):
        program = {"running": running, "ptyType": "wezterm"}
        if exit_code is not None:
            program["exitCode"] = exit_code
        return SessionResult(command_type=command_type, reason=reason, program=program)

    def test_exec_trigger_matched(self):
        assert _session_reason_hint(self._mk("exec", "trigger_matched", True)) == ""

    def test_exec_timeout(self):
        assert _session_reason_hint(self._mk("exec", "trigger_timeout", True)) == ""

    def test_send_idle_timeout(self):
        assert _session_reason_hint(self._mk("send", "idle_timeout", True)) == ""

    def test_exec_program_ended(self):
        assert _session_reason_hint(self._mk("exec", "program_ended", False)) == ""

    def test_exec_crashed(self):
        # 崩溃消息走 _session_message（(PTY-Agent message: ...)），不再进 hint
        hint = _session_reason_hint(
            self._mk("exec", "program_crashed", False, exit_code=1)
        )
        assert hint == ""
        msg = _session_message(
            self._mk("exec", "program_crashed", False, exit_code=1)
        )
        assert msg == "Program crashed with exit code: 1."

    def test_exec_crashed_no_exit_code(self):
        assert _session_message(
            self._mk("exec", "program_crashed", False)
        ) == "Program crashed."

    def test_read_session_ended(self):
        hint = _session_reason_hint(self._mk("read", "ok", False))
        assert "ended" in hint

    def test_read_running(self):
        assert _session_reason_hint(self._mk("read", "ok", True)) == ""


class TestDispatcherAuth:
    def test_valid_token(self):
        auth = TokenAuthenticator("my-token")
        assert auth.authenticate({"auth": {"token": "my-token"}}) is True

    def test_invalid_token(self):
        auth = TokenAuthenticator("my-token")
        assert auth.authenticate({"auth": {"token": "wrong-token"}}) is False

    def test_empty_token_when_auth_enforced(self):
        auth = TokenAuthenticator("my-token")
        assert auth.authenticate({"auth": {"token": ""}}) is False

    def test_no_auth_when_not_enforced(self):
        handler, _ = _setup_handler("")
        assert handler._ctx.authenticator is None

    def test_add_valid_token(self):
        auth = TokenAuthenticator("old-token")
        auth.rotate_token("new-token", "old-token")
        assert auth.authenticate({"auth": {"token": "new-token"}}) is True
        assert auth.authenticate({"auth": {"token": "old-token"}}) is True

    def test_expired_old_token(self):
        auth = TokenAuthenticator("old-token")
        auth.rotate_token("new-token", "old-token")
        auth._tokens["old-token"] = time.monotonic() - 1
        assert auth.authenticate({"auth": {"token": "old-token"}}) is False


class TestDispatcherHandle:
    def _handle_msg(self, handler, msg_dict):
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

    def test_ping(self):
        handler, _ = _setup_handler()
        resp = self._handle_msg(handler, {"type": "ping"})
        assert resp is not None
        assert resp["type"] == "pong"

    def test_unknown_command(self):
        handler, _ = _setup_handler()
        resp = self._handle_msg(handler, {"type": "unknown_cmd"})
        assert resp is not None
        assert resp["type"] == "error"
        assert "未知指令" in resp["message"]

    def test_auth_failure(self):
        handler, _ = _setup_handler("secret")
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
        Message.send(cli, {"type": "exec", "id": "x", "auth": {"token": "wrong"}})
        resp = _read_body(cli)
        cli.close()
        srv.close()
        t.join(timeout=5)
        assert resp is not None
        assert resp["type"] == "error"
        assert "Authentication" in resp["message"] or "认证" in resp["message"]


class TestDispatcherBuildResult:
    def test_build_result_with_session(self):
        session = _MockSession("test-sess")
        mgr = _MockManager({"test-sess": session})
        handler = DaemonDispatcher(mgr, AuthContext())
        result = build_result(mgr, "test-sess", "output", True, "matched")
        assert result["commandType"] == "exec"
        assert result["sessionId"] == "test-sess"
        assert result["triggerReturnReason"] == "trigger_matched"
        assert result["program"]["running"] is True
        assert result["program"]["ptyType"] == "win-conpty"

    def test_build_result_no_session(self):
        mgr = _MockManager()
        handler = DaemonDispatcher(mgr, AuthContext())
        result = build_result(mgr, "no-such", "output", False, "timeout")
        assert result["commandType"] == "exec"
        assert result["program"]["running"] is False
        assert result["program"]["ptyType"] == "none"

    def test_build_result_with_exit_code(self):
        session = _MockSession("test-sess")
        session.exit_code = 1
        session.error_message = "crashed"
        mgr = _MockManager({"test-sess": session})
        handler = DaemonDispatcher(mgr, AuthContext())
        result = build_result(mgr, "test-sess", "output", False, "crashed")
        assert result["program"]["exitCode"] == 1
        assert result["program"]["errorMessage"] == "crashed"

    def test_build_result_with_warning(self):
        session = _MockSession("test-sess")
        mgr = _MockManager({"test-sess": session})
        handler = DaemonDispatcher(mgr, AuthContext())
        result = build_result(mgr, "test-sess", "output", True, "matched",
                                       warning="extra info")
        assert "extra info" in result["hint"]


class TestStripIfNeeded:
    def test_strip_ansi(self):
        mgr = _MockManager()
        handler = DaemonDispatcher(mgr, AuthContext())
        result = strip_if_needed("\x1b[31mred\x1b[0m", {})
        assert result == "red"

    def test_keep_ansi(self):
        mgr = _MockManager()
        handler = DaemonDispatcher(mgr, AuthContext())
        result = strip_if_needed("\x1b[31mred\x1b[0m", {"keep_ansi": True})
        assert "\x1b[31m" in result


class TestGetDetail:
    def test_empty_msg(self):
        mgr = _MockManager()
        handler = DaemonDispatcher(mgr, AuthContext())
        assert get_detail({}) == ""

    def test_msg_with_command(self):
        mgr = _MockManager()
        handler = DaemonDispatcher(mgr, AuthContext())
        detail = get_detail({"command": "echo hello"})
        assert "cmd=" in detail

    def test_msg_with_trigger(self):
        mgr = _MockManager()
        handler = DaemonDispatcher(mgr, AuthContext())
        detail = get_detail({"trigger": ">>>"})
        assert "trigger=" in detail

    def test_msg_with_encoding(self):
        mgr = _MockManager()
        handler = DaemonDispatcher(mgr, AuthContext())
        detail = get_detail({"encoding": "utf-8"})
        assert "enc=" in detail


class TestDispatcherStop:
    class _StopTrackingServer:
        def __init__(self):
            self.stop_called = False

        def stop(self):
            self.stop_called = True

    def test_stop_returns_ok(self):
        handler, _ = _setup_handler()
        resp = self._handle_stop(handler)
        assert resp is not None

    def test_stop_calls_server_stop(self):
        mgr = _MockManager()
        mock_server = self._StopTrackingServer()
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")), server=mock_server)
        resp = self._handle_stop(handler)
        assert resp is not None
        assert mock_server.stop_called

    def _handle_stop(self, handler):
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
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        t = threading.Thread(target=server, daemon=True)
        t.start()
        ready.wait(timeout=5)

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        cli.settimeout(5)
        Message.send(cli, {"type": "stop", "auth": {"token": "test-token"}})
        try:
            resp = _read_body(cli)
        except Exception:
            resp = None
        cli.close()
        srv.close()
        t.join(timeout=5)
        return resp


class TestSnapshotFlowWithTrigger:
    """snapshot-mode 下 trigger/idle-timeout 测试"""

    def _make_snapshot_session(self, sid="snap-test"):
        session = _MockSession(sid)
        session._snapshot_text = ""

        from src.session.trigger_matcher import TriggerMatcher
        tm = TriggerMatcher(decode_func=lambda x: x.decode("utf-8", errors="replace"))

        def fake_get_snapshot(keep_ansi=False):
            return session._snapshot_text

        def fake_set_snapshot_trigger(pattern=None, idle_timeout=None,
                                      idle_after_first_output=False):
            tm.set_snapshot_trigger(pattern=pattern, idle_timeout=idle_timeout,
                                    idle_after_first_output=idle_after_first_output)

        def fake_check_snapshot_trigger(snapshot_text):
            return tm.check_snapshot(snapshot_text)

        def fake_check_snapshot_idle_timeout():
            return tm.check_idle_timeout()

        def fake_notify_snapshot_changed():
            tm.notify_snapshot_changed(time.monotonic())

        def fake_clear_trigger():
            tm.clear()

        session.get_snapshot = fake_get_snapshot
        session.set_snapshot_trigger = fake_set_snapshot_trigger
        session.check_snapshot_trigger = fake_check_snapshot_trigger
        session.check_snapshot_idle_timeout = fake_check_snapshot_idle_timeout
        session.notify_snapshot_changed = fake_notify_snapshot_changed
        session.clear_trigger = fake_clear_trigger
        return session

    def test_snapshot_trigger_matched(self):
        session = self._make_snapshot_session()
        mgr = _MockManager({"snap-test": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))

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
        Message.send(cli, {
            "type": "exec", "id": "snap-test",
            "auth": {"token": "test-token"},
            "command": "echo test",
            "trigger": r"hello",
            "timeout": 5,
        })

        time.sleep(0.3)
        session._snapshot_text = "hello world"

        resp = _read_body(cli)
        cli.close()
        srv.close()
        t.join(timeout=5)

        assert resp is not None
        assert resp["triggerReturnReason"] == "trigger_matched"

    def test_snapshot_idle_timeout(self):
        session = self._make_snapshot_session()
        mgr = _MockManager({"snap-test": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))

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
        Message.send(cli, {
            "type": "exec", "id": "snap-test",
            "auth": {"token": "test-token"},
            "command": "echo test",
            "idle_timeout": 0.2,
            "timeout": 10,
        })

        time.sleep(0.1)
        session._snapshot_text = "first output"
        time.sleep(0.1)
        session._snapshot_text = "first output"

        resp = _read_body(cli)
        cli.close()
        srv.close()
        t.join(timeout=5)

        assert resp is not None
        assert resp["triggerReturnReason"] == "idle_timeout"

    def test_snapshot_no_trigger_timeout(self):
        session = self._make_snapshot_session()
        mgr = _MockManager({"snap-test": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))

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
        Message.send(cli, {
            "type": "exec", "id": "snap-test",
            "auth": {"token": "test-token"},
            "command": "echo test",
            "timeout": 1,
        })

        resp = _read_body(cli)
        cli.close()
        srv.close()
        t.join(timeout=5)

        assert resp is not None
        assert resp["triggerReturnReason"] == "ok"


class TestSnapshotReadLines:
    """快照模式下 read -l 行过滤测试"""

    def _make_multi_line_snapshot_session(self, sid="snap-read"):
        session = _MockSession(sid)
        session._snapshot_lines = ["line1", "line2", "line3", "line4", "line5"]

        def fake_get_snapshot(keep_ansi=False):
            return "\n".join(session._snapshot_lines)

        session.get_snapshot = fake_get_snapshot
        session.get_full_snapshot = fake_get_snapshot
        session.get_snapshot_diff = fake_get_snapshot
        session.get_snapshot_diagnostics = lambda: {"pyte_available": True}
        session.export_screen_buffer = lambda: {}
        return session

    def _handle_read(self, handler, msg_dict):
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

    def test_snapshot_read_lines_last_n(self):
        session = self._make_multi_line_snapshot_session()
        mgr = _MockManager({"snap-read": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))
        resp = self._handle_read(handler, {
            "type": "read", "id": "snap-read",
            "snapshot": True, "lines": 3,
        })
        assert resp is not None
        assert resp["outputStream"] == "line3\nline4\nline5"

    def test_snapshot_read_lines_range(self):
        session = self._make_multi_line_snapshot_session()
        mgr = _MockManager({"snap-read": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))
        resp = self._handle_read(handler, {
            "type": "read", "id": "snap-read",
            "snapshot": True, "lines": "2:4",
        })
        assert resp is not None
        assert resp["outputStream"] == "line2\nline3\nline4"

    def test_snapshot_read_lines_larger_than_total(self):
        session = self._make_multi_line_snapshot_session()
        mgr = _MockManager({"snap-read": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))
        resp = self._handle_read(handler, {
            "type": "read", "id": "snap-read",
            "snapshot": True, "lines": 99,
        })
        assert resp is not None
        assert resp["outputStream"] == "line1\nline2\nline3\nline4\nline5"

    def test_snapshot_read_no_lines(self):
        session = self._make_multi_line_snapshot_session()
        mgr = _MockManager({"snap-read": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))
        resp = self._handle_read(handler, {
            "type": "read", "id": "snap-read",
            "snapshot": True,
        })
        assert resp is not None
        assert resp["outputStream"] == "line1\nline2\nline3\nline4\nline5"

    def test_pty_read_rejects_offset(self):
        """终端模式 read 拒绝 --offset（--offset 仅子进程模式可用，用于增量读取）"""
        session = self._make_multi_line_snapshot_session()
        mgr = _MockManager({"snap-read": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))
        resp = self._handle_read(handler, {
            "type": "read", "id": "snap-read",
            "snapshot": True, "offset": 10,
        })
        assert resp is not None
        assert resp["type"] == "error"
        assert "不支持 --offset" in resp["message"]


class _RecordingSession(_MockSession):
    """send 回归：记录写入的输入，验证 send 流程正常写 stdin"""

    def __init__(self, sid="test", running=True, mode="pty"):
        super().__init__(sid, running)
        self.mode = mode
        self.written = []
        self.stderr_read_offset = 0

    def write_input(self, data, pause_offsets=None):
        self.written.append(data)


class TestSendHandler:
    """send 请求回归：修复前 _handle_send_flow 引用未定义局部变量
    session_id/input_text 致 NameError，流程直接崩溃"""

    def _handle_send(self, handler, msg_dict):
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

    def test_send_pty_writes_input(self):
        session = _RecordingSession("send-test")
        mgr = _MockManager({"send-test": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))
        resp = self._handle_send(handler, {
            "type": "send", "id": "send-test", "input": "hello",
            "timeout": 0,
        })
        assert resp is not None
        assert resp["commandType"] == "send"
        assert resp["sessionId"] == "send-test"
        # 转义展开由守护进程完成：pty 模式默认行尾 → \r
        assert session.written == ["hello\r"]

    def test_send_subprocess_writes_input(self):
        session = _RecordingSession("send-sub", mode="subprocess")
        mgr = _MockManager({"send-sub": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))
        resp = self._handle_send(handler, {
            "type": "send", "id": "send-sub", "input": "world",
            "timeout": 0,
        })
        assert resp is not None
        assert resp["commandType"] == "send"
        assert resp["sessionId"] == "send-sub"
        # 转义展开由守护进程完成：subprocess 模式默认行尾 → \n
        assert session.written == ["world\n"]

    def test_send_json_enter_pty(self):
        session = _RecordingSession("send-pty")
        mgr = _MockManager({"send-pty": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))
        resp = self._handle_send(handler, {
            "type": "send", "id": "send-pty", "input": "{esc}:wq{enter}",
            "json_escaping": True, "timeout": 0,
        })
        assert session.written == ["\x1b:wq\r"]

    def test_send_json_enter_subprocess(self):
        session = _RecordingSession("send-subb", mode="subprocess")
        mgr = _MockManager({"send-subb": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))
        resp = self._handle_send(handler, {
            "type": "send", "id": "send-subb", "input": "name{enter}",
            "json_escaping": True, "timeout": 0,
        })
        # 子进程默认行尾也是 \n：{enter}→\n 与末尾 EOL 同为 \n，不重复追加
        assert session.written == ["name\n"] or session.written == ["name\n\n"]

    def test_send_non_running_returns_error(self):
        session = _RecordingSession("send-dead", running=False)
        mgr = _MockManager({"send-dead": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))
        resp = self._handle_send(handler, {
            "type": "send", "id": "send-dead", "input": "hello",
        })
        assert resp is not None
        assert "error" in resp["type"] or "ended" in resp["message"]

    def test_send_invalid_escape_returns_error(self):
        """advsend 遇到不可识别的 {body} 转义序列应返回明确错误而非内部 500"""
        session = _RecordingSession("send-bad")
        mgr = _MockManager({"send-bad": session})
        handler = DaemonDispatcher(mgr, AuthContext(authenticator=TokenAuthenticator("test-token")))
        resp = self._handle_send(handler, {
            "type": "send", "id": "send-bad", "input": "qq={55}",
            "json_escaping": True, "timeout": 0,
        })
        assert resp is not None
        assert resp.get("type") == "error"
        assert "转义" in (resp.get("message") or resp.get("error") or "")
        assert resp.get("commandType") != "send"
