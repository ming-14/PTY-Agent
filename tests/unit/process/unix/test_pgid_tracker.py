"""PgidProcessTreeTracker 单元测试

测试 process group 进程树追踪的登记、kill_tree、waitpid 收尸
与通知映射。仅 POSIX 平台运行（Windows 无 os.getpgid/killpg）。
"""

import os
import sys
import signal
import subprocess
import time
import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform not in ("linux", "darwin"),
                       reason="process group 仅在 POSIX 平台可用"),
]


@pytest.fixture
def tracker():
    """创建一个已登记 sleep 子进程的 tracker"""
    from src.process.unix.pgid_tracker import PgidProcessTreeTracker
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    t = PgidProcessTreeTracker()
    ok = t.register_root(proc.pid)
    assert ok
    yield t, proc
    if proc.poll() is None:
        proc.kill()
        proc.wait()
    t.close()


class TestRegisterRoot:
    def test_captures_pgid(self, tracker):
        """register_root 应捕获 root 的 pgid"""
        t, proc = tracker
        assert t.pgid == os.getpgid(proc.pid)

    def test_process_list_contains_root(self, tracker):
        """进程列表应包含 root pid"""
        t, proc = tracker
        assert proc.pid in t.get_process_list()

    def test_is_root_alive(self, tracker):
        """存活 root 应判定为 alive"""
        t, proc = tracker
        assert t.is_root_alive() is True


class TestKillTree:
    def test_kill_tree_terminates_process(self, tracker):
        """kill_tree 应终止整个进程树"""
        t, proc = tracker
        t.kill_tree(timeout=5.0)
        proc.wait(timeout=5)
        assert proc.returncode is not None
        assert t.is_root_alive() is False

    def test_get_root_exit_code_after_kill(self, tracker):
        """kill_tree 后 waitpid 收尸应返回退出码"""
        t, proc = tracker
        t.kill_tree(timeout=5.0)
        proc.wait(timeout=5)
        deadline = time.time() + 5.0
        while t.get_root_exit_code() is None and time.time() < deadline:
            time.sleep(0.1)
        assert t.get_root_exit_code() is not None


class TestExitCode:
    def test_extract_exit_code_normal(self):
        """正常退出码提取"""
        from src.process.unix.pgid_tracker import PgidProcessTreeTracker
        proc = subprocess.Popen([sys.executable, "-c", "exit(7)"])
        _, status = os.waitpid(proc.pid, 0)
        assert PgidProcessTreeTracker._extract_exit_code(status) == 7

    def test_extract_exit_code_signaled(self):
        """信号退出码提取（返回负信号值）"""
        from src.process.unix.pgid_tracker import PgidProcessTreeTracker
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        os.kill(proc.pid, signal.SIGTERM)
        _, status = os.waitpid(proc.pid, 0)
        assert PgidProcessTreeTracker._extract_exit_code(status) == -signal.SIGTERM


class TestNotifications:
    def _make_tracker(self):
        from src.process.unix.pgid_tracker import PgidProcessTreeTracker
        t = PgidProcessTreeTracker()
        t.register_root(99999)
        return t

    def test_spawn_notification(self):
        """进程列表新增 → spawn 通知"""
        t = self._make_tracker()
        t._last_pids = []
        t._notifications = []
        import unittest.mock as mock
        with mock.patch.object(t, "get_process_list", return_value=[100, 200]):
            t._detect_process_changes()
        types = [n.type for n in t._notifications]
        assert types == ["spawn"]

    def test_exit_notification(self):
        """进程列表消失 → exit 通知"""
        t = self._make_tracker()
        t._last_pids = [100, 200]
        t._notifications = []
        import unittest.mock as mock
        with mock.patch.object(t, "get_process_list", return_value=[100]):
            t._detect_process_changes()
        types = [n.type for n in t._notifications]
        assert types == ["exit"]

    def test_drain_notifications_clears_queue(self):
        """drain_notifications 应清空队列"""
        t = self._make_tracker()
        t._notifications = [type("N", (), {"type": "spawn"})()]
        items = t.drain_notifications()
        assert len(items) == 1
        assert t.drain_notifications() == []

    def test_crash_notification_on_nonzero_exit(self):
        """root 非 0 退出 → crash 通知"""
        t = self._make_tracker()
        t._reaped = False
        t._exit_code = None
        import unittest.mock as mock
        with mock.patch.object(t, "get_root_exit_code", return_value=3):
            t._check_crash_internal()
        assert len(t._notifications) == 1
        n = t._notifications[0]
        assert n.is_crash() is True
        assert n.exit_code == 3

    def test_exit_notification_on_zero_exit(self):
        """root 0 退出 → exit 通知"""
        t = self._make_tracker()
        t._reaped = False
        t._exit_code = None
        import unittest.mock as mock
        with mock.patch.object(t, "get_root_exit_code", return_value=0):
            t._check_crash_internal()
        assert len(t._notifications) == 1
        assert t._notifications[0].is_exit() is True
