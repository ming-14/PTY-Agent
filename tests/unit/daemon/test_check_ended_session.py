"""check_ended_session 单元测试

回归修复：exec 应允许复用被 kill（tag='history'）的 sid，
仅拒绝自然结束（tag='ended'）的 sid。

覆盖：
- check_ended_session 纯函数（各种 tag 分支）
- ExecHandler：ended 拒绝 / history 放行 / 无历史放行 / 无 history_store 放行
- SendHandler：会话不存在时 ended→"has ended" / history→"not found"
"""

import contextlib

import pytest

from src.execution.utils import check_ended_session
from src.daemon.handlers.exec_handler import ExecHandler
from src.daemon.handlers.send_handler import SendHandler
from src.protocol.message import Message


# ═══════════════════════════════════════════════════════════
# 纯函数测试
# ═══════════════════════════════════════════════════════════

class _FakeHistoryStore:
    def __init__(self, tag):
        self._tag = tag

    def get_session_tag(self, identifier):
        return self._tag


class _FakeManager:
    def __init__(self, tag=None, has_history_store=True):
        self.history_store = _FakeHistoryStore(tag) if has_history_store else None
        self.plugin_registry = None
        self._created = []

    def get_session(self, sid):
        return None

    def match_auto_load(self, command, cwd, env):
        return []

    def get_global_defaults(self):
        return {}

    def create_session(self, *args, **kwargs):
        # 记录被调用 = 放行；抛 KeyError 模拟"走到创建分支"
        self._created.append((args[0], kwargs.get("mode", "pty")))
        raise KeyError("simulated-create")

    def remove_session(self, sid):
        pass


class TestCheckEndedSessionFunction:
    """check_ended_session 纯函数：仅 ended 拒绝，history/None 放行"""

    def test_no_history_store_returns_none(self):
        mgr = _FakeManager(has_history_store=False)
        assert check_ended_session(mgr, "t1") is None

    def test_tag_ended_returns_ended(self):
        mgr = _FakeManager(tag="ended")
        assert check_ended_session(mgr, "t1") == "ended"

    def test_tag_history_returns_none(self):
        """回归修复核心：被 kill 的会话允许复用 sid"""
        mgr = _FakeManager(tag="history")
        assert check_ended_session(mgr, "t1") is None

    def test_tag_none_returns_none(self):
        mgr = _FakeManager(tag=None)
        assert check_ended_session(mgr, "t1") is None

    def test_unknown_tag_returns_none(self):
        mgr = _FakeManager(tag="whatever")
        assert check_ended_session(mgr, "t1") is None


# ═══════════════════════════════════════════════════════════
# Handler 集成测试
# ═══════════════════════════════════════════════════════════

class _CapturingConn:
    """捕获 Message.send 的输出响应体"""

    def __init__(self):
        self.responses = []

    def sendall(self, data):
        import json
        body = json.loads(data.decode("utf-8"))
        self.responses.append(body)

    def close(self):
        pass

    def fileno(self):
        return -1

    def settimeout(self, t):
        pass


class _FakeHandlerCtx:
    def __init__(self, manager):
        self.manager = manager
        self.server = None


@pytest.fixture
def exec_handler():
    return ExecHandler()


class TestExecHandlerReuse:
    """ExecHandler：ended 拒绝，history/None 放行创建"""

    @staticmethod
    def _run(handler, manager, sid="t1"):
        conn = _CapturingConn()
        ctx = _FakeHandlerCtx(manager)
        msg = {"type": "exec", "id": sid, "command": "echo hi", "timeout": 5}
        handler.handle(ctx, conn, msg)
        return conn, manager

    def test_ended_rejected_no_create(self, exec_handler):
        mgr = _FakeManager(tag="ended")
        conn, mgr = self._run(exec_handler, mgr)
        assert mgr._created == []  # 未走到创建
        body = conn.responses[-1]
        assert body is not None
        msg = body.get("message", "")
        assert "ended" in msg  # 拒绝消息

    def test_history_allows_create(self, exec_handler):
        """回归修复核心：被 kill 的 sid 允许重新 exec"""
        mgr = _FakeManager(tag="history")
        conn, mgr = self._run(exec_handler, mgr)
        assert len(mgr._created) == 1  # 放行，走到创建分支
        assert mgr._created[0][0] == "t1"

    def test_no_history_allows_create(self, exec_handler):
        mgr = _FakeManager(tag=None)
        conn, mgr = self._run(exec_handler, mgr)
        assert len(mgr._created) == 1

    def test_no_history_store_allows_create(self, exec_handler):
        mgr = _FakeManager(has_history_store=False)
        conn, mgr = self._run(exec_handler, mgr)
        assert len(mgr._created) == 1

    def test_history_error_not_sent(self, exec_handler):
        """history 放行后不应发送 ended 拒绝消息"""
        mgr = _FakeManager(tag="history")
        conn, _ = self._run(exec_handler, mgr)
        for body in conn.responses:
            if body is None:
                continue
            assert "ended, kill and re-exec" not in body.get("message", "")


class TestSendHandlerNotFound:
    """SendHandler：会话不存在时 ended→"has ended"，history→"not found" """

    @staticmethod
    def _run(manager, sid="t1"):
        conn = _CapturingConn()
        ctx = _FakeHandlerCtx(manager)
        msg = {"type": "send", "id": sid, "input": "ls"}
        SendHandler().handle(ctx, conn, msg)
        return conn

    def test_ended_reports_has_ended(self):
        conn = self._run(_FakeManager(tag="ended"))
        body = conn.responses[-1]
        assert "has ended" in body.get("message", "").lower()

    def test_history_reports_not_found(self):
        conn = self._run(_FakeManager(tag="history"))
        body = conn.responses[-1]
        msg = body.get("message", "")
        assert "not found" in msg.lower()
        assert "has ended" not in msg.lower()

    def test_no_history_reports_not_found(self):
        conn = self._run(_FakeManager(tag=None))
        body = conn.responses[-1]
        assert "not found" in body.get("message", "").lower()
