"""session/manager.py 单元测试"""

import sys
import time

import pytest

from src.session.manager import SessionManager

# spawn 探测命令：Windows 用 cmd wrapper（echo 为内建命令），Unix 直接 echo
_SPAWN_CMD = "cmd /c echo hello" if sys.platform == "win32" else "echo hello"
_SPAWN1 = "cmd /c echo 1" if sys.platform == "win32" else "echo 1"
_SPAWN2 = "cmd /c echo 2" if sys.platform == "win32" else "echo 2"


class _MockSession:
    def __init__(self, sid, running=True):
        self.id = sid
        self.running = running
        self.command = "echo test"
        self.start_time = time.time()

    def stop(self):
        self.running = False

    def start(self):
        self.running = True


class TestSessionManagerCreate:
    def test_create_session(self):
        mgr = SessionManager()
        s = mgr.create_session("test", _SPAWN_CMD)
        assert s is not None
        assert s.id == "test"

    def test_create_duplicate_raises(self):
        mgr = SessionManager()
        mgr.create_session("test", _SPAWN_CMD)
        with pytest.raises(KeyError, match="已存在"):
            mgr.create_session("test", _SPAWN_CMD)

    def test_create_empty_id_raises(self):
        mgr = SessionManager()
        with pytest.raises(ValueError, match="非空字符串"):
            mgr.create_session("", _SPAWN_CMD)

    def test_create_none_id_raises(self):
        mgr = SessionManager()
        with pytest.raises(ValueError, match="非空字符串"):
            mgr.create_session(None, _SPAWN_CMD)


class TestSessionManagerPreHold:
    """create_session 创建期预持有接线（缺陷2 修复）：

    pre_hold 在 start 前调用，把"create_session 返回 → handler 首个 hold"
    的空窗并入持有；首个 hold/acquire_hold 消费预持有。"""

    def test_create_session_pre_holds(self):
        mgr = SessionManager()
        s = mgr.create_session("test", _SPAWN_CMD)
        try:
            assert s._creation_hold is True
            assert s._hold_count == 1
        finally:
            # 消费预持有并归还计数（模拟 handler 流程），避免持有泄漏
            s.acquire_hold()
            s.release_hold()

    def test_hold_consumes_creation_hold(self):
        mgr = SessionManager()
        s = mgr.create_session("test", _SPAWN_CMD)
        with s.hold():
            assert s._creation_hold is False
            assert s._hold_count == 1
        assert s._hold_count == 0


class TestSessionManagerGet:
    def test_get_existing_session(self):
        mgr = SessionManager()
        mgr.create_session("test", _SPAWN_CMD)
        s = mgr.get_session("test")
        assert s is not None
        assert s.id == "test"

    def test_get_nonexistent_session(self):
        mgr = SessionManager()
        assert mgr.get_session("no-such") is None


class TestSessionManagerList:
    def test_list_empty(self):
        mgr = SessionManager()
        assert mgr.list_sessions() == []

    def test_list_sessions(self):
        mgr = SessionManager()
        mgr.create_session("s1", _SPAWN1)
        mgr.create_session("s2", _SPAWN2)
        sessions = mgr.list_sessions()
        ids = [s["id"] for s in sessions]
        assert "s1" in ids
        assert "s2" in ids

    def test_list_includes_ended_sessions(self):
        mgr = SessionManager()
        s = mgr.create_session("test", _SPAWN_CMD)
        s.running = False
        sessions = mgr.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["running"] is False

    def test_list_includes_startTime(self):
        mgr = SessionManager()
        before = time.time()
        mgr.create_session("s1", _SPAWN1)
        after = time.time()
        sessions = mgr.list_sessions()
        assert len(sessions) == 1
        assert "startTime" in sessions[0]
        assert before <= sessions[0]["startTime"] <= after

    def test_list_startTime_is_float(self):
        mgr = SessionManager()
        mgr.create_session("s1", _SPAWN1)
        sessions = mgr.list_sessions()
        assert isinstance(sessions[0]["startTime"], float)

    def test_list_removes_naturally_ended(self):
        """自然结束的会话会被归档移除（Web 前端通过历史列表查看）"""
        mgr = SessionManager()
        s = mgr.create_session("ended-1", [sys.executable, "-c", "import sys; sys.exit(0)"])
        for _ in range(100):
            if not s.running:
                break
            time.sleep(0.05)
        if s.running:
            s.stop()
        for _ in range(50):
            sessions = mgr.list_sessions()
            if not any(s_["id"] == "ended-1" for s_ in sessions):
                break
            time.sleep(0.1)
        sessions = mgr.list_sessions()
        assert not any(s_["id"] == "ended-1" for s_ in sessions)


class TestSessionManagerRemove:
    def test_remove_session(self):
        mgr = SessionManager()
        mgr.create_session("test", _SPAWN_CMD)
        mgr.remove_session("test")
        assert mgr.get_session("test") is None

    def test_remove_nonexistent_no_error(self):
        mgr = SessionManager()
        mgr.remove_session("no-such")


class TestSessionManagerStopAll:
    def test_stop_all(self):
        mgr = SessionManager()
        mgr.create_session("s1", _SPAWN1)
        mgr.create_session("s2", _SPAWN2)
        mgr.stop_all()
        assert mgr.get_session("s1") is None
        assert mgr.get_session("s2") is None
