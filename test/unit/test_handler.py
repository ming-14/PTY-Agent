"""RequestHandler 单元测试

测试请求处理器的认证、验证、消息派发、各 _handle_* 方法。
handler.handle(msg) 直接返回 dict，无需 TCP 连接。
"""

import json
import time
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.daemon.handler import RequestHandler, _validate_field


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


def _setup_handler(auth_token="test-token", sessions=None, server=None):
    """创建 handler"""
    mgr = _MockManager(sessions)
    handler = RequestHandler(mgr, auth_token=auth_token, server=server)
    return handler, mgr


def _with_token(msg: dict) -> dict:
    """注入有效令牌（模拟服务器从信箱槽位注入）"""
    msg = dict(msg)
    msg["token"] = "test-token"
    return msg


class TestValidateField:
    """_validate_field 测试（新版本返回 error dict 或 None）"""

    def test_valid_field(self):
        """字段长度未超限返回 None"""
        assert _validate_field("short", "name", 100) is None

    def test_overlimit_field(self):
        """字段长度超限返回 error dict"""
        err = _validate_field("x" * 200, "name", 100)
        assert err is not None
        assert err["type"] == "error"
        assert "过长" in err["error"]

    def test_none_field(self):
        """None 字段通过验证"""
        assert _validate_field(None, "name", 100) is None

    def test_non_string_field(self):
        """非字符串字段通过验证"""
        assert _validate_field(123, "name", 100) is None


class TestRequestHandlerAuth:
    """RequestHandler 认证测试"""

    def test_valid_token(self):
        """有效令牌通过认证"""
        handler, _ = _setup_handler("my-token")
        assert handler._is_token_valid("my-token") is True

    def test_invalid_token(self):
        """无效令牌认证失败"""
        handler, _ = _setup_handler("my-token")
        assert handler._is_token_valid("wrong-token") is False

    def test_empty_token_when_auth_enforced(self):
        """认证启用时空令牌失败"""
        handler, _ = _setup_handler("my-token")
        assert handler._is_token_valid("") is False

    def test_no_auth_when_not_enforced(self):
        """认证未启用时空令牌通过"""
        handler, _ = _setup_handler("")
        assert handler._auth_enforced is False

    def test_add_valid_token(self):
        """添加新令牌后旧令牌在宽限期内有效"""
        handler, _ = _setup_handler("old-token")
        handler.add_valid_token("new-token", "old-token")
        assert handler._is_token_valid("new-token") is True
        assert handler._is_token_valid("old-token") is True

    def test_expired_old_token(self):
        """过期旧令牌认证失败"""
        handler, _ = _setup_handler("old-token")
        handler.add_valid_token("new-token", "old-token")
        handler._auth_tokens["old-token"] = time.monotonic() - 1
        assert handler._is_token_valid("old-token") is False


class TestRequestHandlerHandle:
    """RequestHandler.handle 消息派发测试（直接返回 dict，无 TCP）"""

    def test_ping(self):
        """ping 返回 pong"""
        handler, _ = _setup_handler()
        resp = handler.handle({"type": "ping"})
        assert resp["type"] == "pong"

    def test_unknown_command(self):
        """未知指令返回 error"""
        handler, _ = _setup_handler()
        resp = handler.handle(_with_token({"type": "unknown_cmd"}))
        assert resp["type"] == "error"
        assert "未知指令" in resp["error"]

    def test_auth_failure(self):
        """认证失败返回 error"""
        handler, _ = _setup_handler("secret")
        resp = handler.handle({"type": "exec", "id": "x", "token": "wrong"})
        assert resp["type"] == "error"
        assert "认证" in resp["error"]

    def test_ping_no_auth_needed(self):
        """ping 不需要认证"""
        handler, _ = _setup_handler("secret")
        resp = handler.handle({"type": "ping"})
        assert resp["type"] == "pong"

    def test_stop_no_auth_needed(self):
        """stop 不需要认证"""
        handler, _ = _setup_handler("secret")
        resp = handler.handle({"type": "stop"})
        assert resp["type"] == "ok"

    def test_handle_list(self):
        """list 返回会话列表"""
        s = _MockSession("s1")
        handler, _ = _setup_handler(sessions={"s1": s})
        resp = handler.handle(_with_token({"type": "list"}))
        assert resp["type"] == "ok"
        assert len(resp["sessions"]) == 1

    def test_handle_kill(self):
        """kill 终止会话"""
        s = _MockSession("s1")
        handler, _ = _setup_handler(sessions={"s1": s})
        resp = handler.handle(_with_token({"type": "kill", "id": "s1"}))
        assert resp["type"] == "ok"
        assert "已终止" in resp.get("note", "")

    def test_handle_kill_nonexistent(self):
        """kill 不存在的会话返回 error"""
        handler, _ = _setup_handler()
        resp = handler.handle(_with_token({"type": "kill", "id": "nonexistent"}))
        assert resp["type"] == "error"

    def test_handle_exec_missing_id(self):
        """exec 缺少 id 返回 error"""
        handler, _ = _setup_handler()
        resp = handler.handle(_with_token({"type": "exec", "command": "echo test"}))
        assert resp["type"] == "error"

    def test_handle_exec_missing_command(self):
        """exec 缺少 command 返回 error"""
        handler, _ = _setup_handler()
        resp = handler.handle(_with_token({"type": "exec", "id": "test"}))
        assert resp["type"] == "error"

    def test_handle_exec_creates_session(self):
        """exec 创建新会话"""
        handler, _ = _setup_handler()
        with patch.object(handler, '_run_trigger_flow',
                          return_value={"type": "exec", "session_id": "new-sess"}):
            resp = handler.handle(_with_token({
                "type": "exec", "id": "new-sess", "command": "python",
                "trigger": ">>>", "timeout": 10,
            }))
            assert resp["type"] == "exec"

    def test_handle_exec_passes_shell_to_session(self):
        """exec 的 --shell 参数传递到 create_session"""
        handler, mgr = _setup_handler()
        with patch.object(handler, '_run_trigger_flow',
                          return_value={"type": "exec", "session_id": "s1"}):
            with patch.object(mgr, 'create_session', wraps=mgr.create_session) as spy:
                handler.handle(_with_token({
                    "type": "exec", "id": "s1", "command": "pwsh --help",
                    "shell": "pwsh", "timeout": 5,
                }))
                spy.assert_called_once()
                _, kwargs = spy.call_args
                assert kwargs.get("shell") == "pwsh", f"shell not passed: {kwargs}"

    def test_handle_read(self):
        """read 读取输出"""
        s = _MockSession("s1")
        handler, _ = _setup_handler(sessions={"s1": s})
        resp = handler.handle(_with_token({"type": "read", "id": "s1"}))
        assert resp["type"] == "read" or resp["type"] == "result"
        assert "output" in resp

    def test_handle_closewin(self):
        """closewin 关闭窗口"""
        s = _MockSession("s1")
        handler, _ = _setup_handler(sessions={"s1": s})
        resp = handler.handle(_with_token({"type": "closewin", "id": "s1", "hwnd": 1234}))
        assert resp["type"] == "ok"


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
        """会话不存在时构建 result"""
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
        assert result["type"] == "result"
        assert result["trigger_matched"] is True
        assert result["reason"] == "matched"


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


class TestRequestHandlerStop:
    """RequestHandler stop 命令测试（新版本 handler.handle 返回 ok）"""

    class _StopTrackingServer:
        def __init__(self):
            self.stop_called = False

        def stop(self):
            self.stop_called = True

    def test_stop_returns_ok(self):
        """stop 命令返回 ok 响应"""
        handler, _ = _setup_handler()
        resp = handler.handle({"type": "stop"})
        assert resp is not None
        assert resp["type"] == "ok"

    def test_stop_no_name_error(self):
        """stop 命令不抛 NameError（regression: socket 未导入）"""
        handler, _ = _setup_handler()
        resp = handler.handle({"type": "stop"})
        assert resp["type"] != "error", f"stop 命令返回了 error: {resp}"