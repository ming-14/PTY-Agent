"""SessionManager 单元测试

测试会话管理器的创建、获取、列出、移除、并发安全。
"""

import sys
import time
import threading
import pytest

from src.session.manager import SessionManager


class _MockPty:
    """模拟 PseudoTerminal"""

    def __init__(self):
        self._processes = []
        self._closed = False
        self._read_event = threading.Event()

    def get_process_list(self):
        return list(self._processes)

    def get_exit_code(self):
        return None

    def read(self, size):
        self._read_event.wait(timeout=30)
        return b""

    def drain(self, max_bytes=65536):
        return b""

    def close(self):
        self._closed = True
        self._read_event.set()

    def write(self, data):
        pass

    def poll_gui_windows(self):
        return []

    def get_child_process_exit_code(self, pid):
        return 0

    def get_type(self):
        return "mock"


class TestSessionManagerCreate:
    """SessionManager.create_session 测试"""

    @pytest.fixture
    def mgr(self, monkeypatch):
        """创建 SessionManager 并 mock create_pty"""
        def _mock_create_pty(*args, **kwargs):
            return _MockPty()
        monkeypatch.setattr("src.session.session.create_pty", _mock_create_pty)
        return SessionManager()

    def test_create_session(self, mgr):
        """创建会话成功"""
        s = mgr.create_session("test-1", [sys.executable, "-c", "pass"])
        assert s is not None
        assert s.id == "test-1"
        s.stop()

    def test_create_duplicate_raises(self, mgr):
        """重复 ID 抛出 KeyError"""
        s = mgr.create_session("dup", [sys.executable, "-c", "pass"])
        with pytest.raises(KeyError):
            mgr.create_session("dup", [sys.executable, "-c", "pass"])
        s.stop()

    def test_create_empty_id_raises(self, mgr):
        """空 ID 抛出 ValueError"""
        with pytest.raises(ValueError):
            mgr.create_session("", [sys.executable, "-c", "pass"])

    def test_create_none_id_raises(self, mgr):
        """None ID 抛出 ValueError"""
        with pytest.raises(ValueError):
            mgr.create_session(None, [sys.executable, "-c", "pass"])


class TestSessionManagerGet:
    """SessionManager.get_session 测试"""

    @pytest.fixture
    def mgr(self, monkeypatch):
        def _mock_create_pty(*args, **kwargs):
            return _MockPty()
        monkeypatch.setattr("src.session.session.create_pty", _mock_create_pty)
        return SessionManager()

    def test_get_existing(self, mgr):
        """获取存在的会话"""
        s = mgr.create_session("get-test", [sys.executable, "-c", "pass"])
        assert mgr.get_session("get-test") is s
        s.stop()

    def test_get_nonexistent(self, mgr):
        """获取不存在的会话返回 None"""
        assert mgr.get_session("no-such") is None


class TestSessionManagerList:
    """SessionManager.list_sessions 测试"""

    @pytest.fixture
    def mgr(self, monkeypatch):
        def _mock_create_pty(*args, **kwargs):
            return _MockPty()
        monkeypatch.setattr("src.session.session.create_pty", _mock_create_pty)
        return SessionManager()

    def test_list_empty(self, mgr):
        """无会话时返回空列表"""
        assert mgr.list_sessions() == []

    def test_list_sessions(self, mgr):
        """列出活跃会话"""
        s1 = mgr.create_session("ls-1", [sys.executable, "-c", "import time; time.sleep(30)"])
        s2 = mgr.create_session("ls-2", [sys.executable, "-c", "import time; time.sleep(30)"])
        time.sleep(0.3)
        result = mgr.list_sessions()
        ids = [s["id"] for s in result]
        assert "ls-1" in ids
        assert "ls-2" in ids
        s1.stop()
        s2.stop()

    def test_list_cleans_ended(self, mgr):
        """列出时清理已结束的会话"""
        s = mgr.create_session("ended-1", [sys.executable, "-c", "import sys; sys.exit(0)"])
        for _ in range(100):
            if not s.running:
                break
            time.sleep(0.05)
        if s.running:
            s.stop()
        mgr.list_sessions()
        assert mgr.get_session("ended-1") is None


class TestSessionManagerRemove:
    """SessionManager.remove_session 测试"""

    @pytest.fixture
    def mgr(self, monkeypatch):
        def _mock_create_pty(*args, **kwargs):
            return _MockPty()
        monkeypatch.setattr("src.session.session.create_pty", _mock_create_pty)
        return SessionManager()

    def test_remove_existing(self, mgr):
        """移除存在的会话"""
        s = mgr.create_session("rm-1", [sys.executable, "-c", "pass"])
        mgr.remove_session("rm-1")
        assert mgr.get_session("rm-1") is None

    def test_remove_nonexistent(self, mgr):
        """移除不存在的会话不抛异常"""
        mgr.remove_session("no-such")

    def test_stop_all(self, mgr):
        """停止所有会话"""
        s1 = mgr.create_session("sa-1", [sys.executable, "-c", "import time; time.sleep(30)"])
        s2 = mgr.create_session("sa-2", [sys.executable, "-c", "import time; time.sleep(30)"])
        mgr.stop_all()
        assert mgr.get_session("sa-1") is None
        assert mgr.get_session("sa-2") is None