"""RequestHandler 单元测试（旧版，保留兼容）

测试请求处理器的认证、验证、消息派发、各 _handle_* 方法。
使用 mock 替代 TCP 连接和 SessionManager。

注意：新版测试在 test/unit/daemon/test_handler.py，此文件保留不删除。
"""

import json
import time
import socket
import threading
import pytest

from src.daemon.handler import RequestHandler, _validate_field, _map_reason, _build_hint
from src.protocol.message import Message
from src.auth.token import TokenAuthenticator


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
    def __init__(self, sid="test", running=True, pty_type="win-conpty"):
        self.id = sid
        self.uid = "uid-test"
        self.running = running
        self.command = "echo test"
        self.start_time = time.time()
        self.exit_code = None
        self.error_message = None
        self.pty_type = pty_type
        self.output_offset = 10
        self.pending_event_count = 0
        self.gui_windows = []
        self.processes = []
        self.snapshot_mode = False
        self.client_config = {}
        self.event_history = type("EH", (), {"history_count": 0})()
        self._screen = type("Screen", (), {"feed_count": 0, "wait_for_change": lambda *a, **kw: None})()

    def get_output(self, **kwargs):
        return "test output"

    def get_snapshot(self, keep_ansi=False):
        return "snapshot content"

    def get_snapshot_diagnostics(self):
        return {"pyte_available": True}

    def write_input(self, data):
        pass

    def set_trigger(self, *args, **kwargs):
        pass

    def wait_for_trigger(self, timeout=None, **kwargs):
        return True, "matched"

    def clear_trigger(self):
        pass

    def wait_for_initial_output(self, timeout=1.0):
        return True

    def consume_events(self):
        return []

    def get_all_events(self, **kwargs):
        return []

    def check_event_existence(self, ev):
        return False

    def close_window(self, hwnd):
        return True

    def perform_mouse_action(self, action):
        return {"performed": True}

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


def _setup_handler_and_conn(auth_token="test-token", sessions=None):
    mgr = _MockManager(sessions)
    authenticator = TokenAuthenticator(auth_token) if auth_token else None
    handler = RequestHandler(mgr, authenticator=authenticator)
    return handler, mgr


class TestValidateField:
    def test_valid_field(self):
        conn = _MockConn()
        assert _validate_field("short", "name", 100, conn) is True

    def test_overlimit_field(self):
        conn = _MockConn()
        assert _validate_field("x" * 200, "name", 100, conn) is False

    def test_none_field(self):
        conn = _MockConn()
        assert _validate_field(None, "name", 100, conn) is True

    def test_non_string_field(self):
        conn = _MockConn()
        assert _validate_field(123, "name", 100, conn) is True


class TestRequestHandlerAuth:
    def test_valid_token(self):
        handler, _ = _setup_handler_and_conn("my-token")
        assert handler._authenticator.authenticate({"token": "my-token"}) is True

    def test_invalid_token(self):
        handler, _ = _setup_handler_and_conn("my-token")
        assert handler._authenticator.authenticate({"token": "wrong-token"}) is False

    def test_empty_token_when_auth_enforced(self):
        handler, _ = _setup_handler_and_conn("my-token")
        assert handler._authenticator.authenticate({"token": ""}) is False

    def test_no_auth_when_not_enforced(self):
        handler, _ = _setup_handler_and_conn("")
        assert handler._authenticator is None

    def test_add_valid_token(self):
        handler, _ = _setup_handler_and_conn("old-token")
        handler._authenticator.rotate_token("new-token", "old-token")
        assert handler._authenticator.authenticate({"token": "new-token"}) is True
        assert handler._authenticator.authenticate({"token": "old-token"}) is True

    def test_expired_old_token(self):
        handler, _ = _setup_handler_and_conn("old-token")
        handler._authenticator.rotate_token("new-token", "old-token")
        handler._authenticator._tokens["old-token"] = time.monotonic() - 1
        assert handler._authenticator.authenticate({"token": "old-token"}) is False


class TestRequestHandlerHandle:
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
        msg_dict["token"] = "test-token"
        is_internal = msg_dict.get("type") in ("ping", "stop")
        Message.send(cli, msg_dict, skip_sign=is_internal)
        resp = Message.recv(cli, skip_sign=is_internal)
        cli.close()
        srv.close()
        t.join(timeout=5)
        return resp

    def test_ping(self):
        handler, _ = _setup_handler_and_conn()
        resp = self._handle_msg(handler, {"type": "ping"})
        assert resp is not None
        assert resp["type"] == "pong"

    def test_unknown_command(self):
        handler, _ = _setup_handler_and_conn()
        resp = self._handle_msg(handler, {"type": "unknown_cmd"})
        assert resp is not None
        assert resp["type"] == "error"
        assert "未知指令" in resp["error"]

    def test_auth_failure(self):
        handler, _ = _setup_handler_and_conn("secret")
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
        Message.send(cli, {"type": "exec", "id": "x", "token": "wrong"})
        resp = Message.recv(cli)
        cli.close()
        srv.close()
        t.join(timeout=5)
        assert resp is not None
        assert resp["type"] == "error"
        assert "Authentication" in resp["error"] or "认证" in resp["error"]


class TestRequestHandlerMouse:
    def test_mouse_performed(self):
        session = _MockSession("test-sess", running=True, pty_type="conpty")
        session.snapshot_mode = True
        mgr = _MockManager({"test-sess": session})
        handler = RequestHandler(mgr)
        resp = TestRequestHandlerHandle()._handle_msg(
            handler,
            {"type": "mouse", "id": "test-sess", "action": "click", "coords": {"col": 1, "row": 1}},
        )
        assert resp is not None
        assert resp.get("commandType") == "mouse"
        assert resp.get("performed") is True
        assert resp.get("sessionId") == "test-sess"

    def test_mouse_session_not_found(self):
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        resp = TestRequestHandlerHandle()._handle_msg(
            handler,
            {"type": "mouse", "id": "missing", "action": "click", "coords": {"col": 1, "row": 1}},
        )
        assert resp is not None
        assert resp.get("type") == "error"


class TestRequestHandlerBuildResult:
    def test_build_result_with_session(self):
        session = _MockSession("test-sess")
        mgr = _MockManager({"test-sess": session})
        handler = RequestHandler(mgr)
        result = handler._build_result("test-sess", "output", "", True, "matched")
        assert result["commandType"] == "exec"
        assert result["sessionId"] == "test-sess"
        assert result["triggerReturnReason"] == "trigger_matched"
        assert result["program"]["running"] is True
        assert result["program"]["ptyType"] == "win-conpty"

    def test_build_result_no_session(self):
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        result = handler._build_result("no-such", "output", "", False, "timeout")
        assert result["commandType"] == "exec"
        assert result["program"]["running"] is False
        assert result["program"]["ptyType"] == "none"

    def test_build_result_with_exit_code(self):
        session = _MockSession("test-sess")
        session.exit_code = 1
        session.error_message = "crashed"
        mgr = _MockManager({"test-sess": session})
        handler = RequestHandler(mgr)
        result = handler._build_result("test-sess", "output", "", False, "crashed")
        assert result["program"]["exitCode"] == 1
        assert result["program"]["errorMessage"] == "crashed"


class TestRequestHandlerStrip:
    def test_strip_ansi(self):
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        result = handler._strip_if_needed("\x1b[31mred\x1b[0m", {})
        assert result == "red"

    def test_keep_ansi(self):
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        result = handler._strip_if_needed("\x1b[31mred\x1b[0m", {"keep_ansi": True})
        assert "\x1b[31m" in result


class TestRequestHandlerGetDetail:
    def test_empty_msg(self):
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        assert handler._get_detail({}) == ""

    def test_msg_with_command(self):
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        detail = handler._get_detail({"command": "echo hello"})
        assert "cmd=" in detail

    def test_msg_with_trigger(self):
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        detail = handler._get_detail({"trigger": ">>>"})
        assert "trigger=" in detail


class TestRequestHandlerStop:
    class _StopTrackingServer:
        def __init__(self):
            self.stop_called = False

        def stop(self):
            self.stop_called = True

    def test_stop_returns_ok(self):
        handler, _ = _setup_handler_and_conn()
        resp = self._handle_stop(handler)
        assert resp is not None
        assert resp.get("commandType") == "stop" or resp.get("type") == "ok"

    def test_stop_calls_server_stop(self):
        mgr = _MockManager()
        mock_server = self._StopTrackingServer()
        handler = RequestHandler(mgr, authenticator=TokenAuthenticator("test-token"), server=mock_server)
        resp = self._handle_stop(handler)
        assert resp is not None
        assert mock_server.stop_called

    def test_stop_no_name_error(self):
        handler, _ = _setup_handler_and_conn()
        resp = self._handle_stop(handler)
        assert resp is not None
        assert resp.get("type") != "error", f"stop 命令返回了 error: {resp}"

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
        Message.send(cli, {"type": "stop", "token": "test-token"})
        try:
            resp = Message.recv(cli)
        except Exception:
            resp = None
        cli.close()
        srv.close()
        t.join(timeout=5)
        return resp
