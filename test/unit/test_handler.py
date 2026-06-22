"""RequestHandler 单元测试

测试请求处理器的认证、验证、消息派发、各 _handle_* 方法。
使用 mock 替代 TCP 连接和 SessionManager。
"""

import json
import time
import socket
import threading
import pytest

from src.daemon.handler import RequestHandler, _validate_field
from src.protocol.message import Message


class _MockConn:
    """模拟 TCP 连接，记录发送的消息"""

    def __init__(self):
        self.sent_messages = []
        self._recv_data = b""
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
    """模拟 Session"""

    def __init__(self, sid="test", running=True):
        self.id = sid
        self.running = running
        self.command = "echo test"
        self.start_time = time.time()
        self.exit_code = None
        self.error_message = None
        self.pty_type = "subprocess"
        self.output_offset = 10
        self.pending_event_count = 0
        self.gui_windows = []
        self.processes = []

    def get_output(self, **kwargs):
        return "test output"

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

    def stop(self):
        self.running = False


class _MockManager:
    """模拟 SessionManager"""

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
    """创建 handler 和 mock 连接"""
    mgr = _MockManager(sessions)
    handler = RequestHandler(mgr, auth_token=auth_token)
    return handler, mgr


class TestValidateField:
    """_validate_field 测试"""

    def test_valid_field(self):
        """字段长度未超限返回 True"""
        conn = _MockConn()
        assert _validate_field("short", "name", 100, conn) is True

    def test_overlimit_field(self):
        """字段长度超限返回 False"""
        conn = _MockConn()
        assert _validate_field("x" * 200, "name", 100, conn) is False

    def test_none_field(self):
        """None 字段通过验证"""
        conn = _MockConn()
        assert _validate_field(None, "name", 100, conn) is True

    def test_non_string_field(self):
        """非字符串字段通过验证"""
        conn = _MockConn()
        assert _validate_field(123, "name", 100, conn) is True


class TestRequestHandlerAuth:
    """RequestHandler 认证测试"""

    def test_valid_token(self):
        """有效令牌通过认证"""
        handler, _ = _setup_handler_and_conn("my-token")
        assert handler._is_token_valid("my-token") is True

    def test_invalid_token(self):
        """无效令牌认证失败"""
        handler, _ = _setup_handler_and_conn("my-token")
        assert handler._is_token_valid("wrong-token") is False

    def test_empty_token_when_auth_enforced(self):
        """认证启用时空令牌失败"""
        handler, _ = _setup_handler_and_conn("my-token")
        assert handler._is_token_valid("") is False

    def test_no_auth_when_not_enforced(self):
        """认证未启用时空令牌通过"""
        handler, _ = _setup_handler_and_conn("")
        assert handler._auth_enforced is False

    def test_add_valid_token(self):
        """添加新令牌后旧令牌在宽限期内有效"""
        handler, _ = _setup_handler_and_conn("old-token")
        handler.add_valid_token("new-token", "old-token")
        assert handler._is_token_valid("new-token") is True
        assert handler._is_token_valid("old-token") is True

    def test_expired_old_token(self):
        """过期旧令牌认证失败"""
        handler, _ = _setup_handler_and_conn("old-token")
        handler.add_valid_token("new-token", "old-token")
        handler._auth_tokens["old-token"] = time.monotonic() - 1
        assert handler._is_token_valid("old-token") is False


class TestRequestHandlerHandle:
    """RequestHandler.handle 消息派发测试"""

    def _handle_msg(self, handler, msg_dict):
        """通过真实 TCP 连接测试 handle"""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        result = [None]

        def server():
            conn, _ = srv.accept()
            try:
                handler.handle(conn, ("127.0.0.1", 0))
            finally:
                conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        msg_dict["token"] = "test-token"
        Message.send(cli, msg_dict)
        resp = Message.recv(cli)
        cli.close()
        srv.close()
        t.join(timeout=5)
        return resp

    def test_ping(self):
        """ping 返回 pong"""
        handler, _ = _setup_handler_and_conn()
        resp = self._handle_msg(handler, {"type": "ping"})
        assert resp is not None
        assert resp["type"] == "pong"

    def test_unknown_command(self):
        """未知指令返回 error"""
        handler, _ = _setup_handler_and_conn()
        resp = self._handle_msg(handler, {"type": "unknown_cmd"})
        assert resp is not None
        assert resp["type"] == "error"
        assert "未知指令" in resp["error"]

    def test_auth_failure(self):
        """认证失败返回 error"""
        handler, _ = _setup_handler_and_conn("secret")
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def server():
            conn, _ = srv.accept()
            try:
                handler.handle(conn, ("127.0.0.1", 0))
            finally:
                conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        Message.send(cli, {"type": "exec", "id": "x", "token": "wrong"})
        resp = Message.recv(cli)
        cli.close()
        srv.close()
        t.join(timeout=5)
        assert resp is not None
        assert resp["type"] == "error"
        assert "认证" in resp["error"]


class TestRequestHandlerBuildResult:
    """RequestHandler._build_result 测试"""

    def test_build_result_basic(self):
        """构建基本 result 响应（v4 格式）"""
        session = _MockSession("test-sess")
        mgr = _MockManager({"test-sess": session})
        handler = RequestHandler(mgr)
        result = handler._build_result("test-sess", "output", True, "matched")
        assert result["type"] == "result"
        assert result["session_id"] == "test-sess"
        assert result["output"] == "output"
        assert result["trigger_matched"] is True
        assert result["reason"] == "matched"
        assert result["program"]["running"] is True

    def test_build_result_no_session(self):
        """会话不存在时构建 result（None session 已安全处理，不再抛 AttributeError）"""
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        result = handler._build_result("no-such", "output", False, "timeout")
        assert result["type"] == "result"
        assert result["session_id"] == "no-such"
        assert result["program"]["running"] is False
        assert result["program"]["pty_type"] == "none"

    def test_build_result_with_events(self):
        """构建含事件的 result"""
        session = _MockSession("test-sess")
        mgr = _MockManager({"test-sess": session})
        handler = RequestHandler(mgr)
        result = handler._build_result("test-sess", "output", True, "matched",
                                       consume_events=True)
        # MockSession.consume_events 返回空列表 → debug 不出现
        # 此处验证没有 debug 时响应仍正常，不抛异常
        assert result["type"] == "result"
        assert result["trigger_matched"] is True
        assert result["reason"] == "matched"


class TestRequestHandlerStrip:
    """RequestHandler._strip_if_needed 测试"""

    def test_strip_ansi(self):
        """默认过滤 ANSI"""
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        result = handler._strip_if_needed("\x1b[31mred\x1b[0m", {})
        assert result == "red"

    def test_keep_ansi(self):
        """keep_ansi=True 保留 ANSI"""
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        result = handler._strip_if_needed("\x1b[31mred\x1b[0m", {"keep_ansi": True})
        assert "\x1b[31m" in result


class TestRequestHandlerGetDetail:
    """RequestHandler._get_detail 测试"""

    def test_empty_msg(self):
        """空消息返回空字符串"""
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        assert handler._get_detail({}) == ""

    def test_msg_with_command(self):
        """含 command 的消息"""
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        detail = handler._get_detail({"command": "echo hello"})
        assert "cmd=" in detail

    def test_msg_with_trigger(self):
        """含 trigger 的消息"""
        mgr = _MockManager()
        handler = RequestHandler(mgr)
        detail = handler._get_detail({"trigger": ">>>"})
        assert "trigger=" in detail