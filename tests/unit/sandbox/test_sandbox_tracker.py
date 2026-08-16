"""src/sandbox/tracker.py 单元测试（mock manager）"""

import sys

import pytest

# win-sandbox 为 Windows 原生组件；非 Windows 平台模块级跳过（import 前）
if sys.platform != "win32":
    pytest.skip("win-sandbox 仅支持 Windows", allow_module_level=True)

from src.sandbox.tracker import SandboxProcessTreeTracker
from src.process.base import ProcessNotification, NOTIF_SPAWN


class _FakeManager:
    def __init__(self):
        self.terminated = None
        self.notifs = [ProcessNotification(NOTIF_SPAWN, pid=9)]
        self.alive = True
        self.root_exit = None
        self.exit_code = 7
        self.list = [1, 2, 3]
        self.closed = False

    def terminate(self, exit_code=1):
        self.terminated = exit_code

    def drain_notifications(self):
        items = self.notifs
        self.notifs = []
        return items

    def is_root_alive(self):
        return self.alive

    def get_exit_code(self):
        return self.root_exit

    def get_process_list(self):
        return self.list

    def get_process_exit_code(self, pid):
        return self.exit_code

    def close(self):
        self.closed = True


@pytest.fixture
def tracker():
    mgr = _FakeManager()
    t = SandboxProcessTreeTracker(mgr)
    return t, mgr


class TestTrackerPort:
    def test_register_root(self, tracker):
        t, mgr = tracker
        assert t.register_root(1234) is True

    def test_get_process_list(self, tracker):
        t, mgr = tracker
        assert t.get_process_list() == [1, 2, 3]

    def test_is_root_alive(self, tracker):
        t, mgr = tracker
        assert t.is_root_alive() is True

    def test_kill_tree_delegates_terminate(self, tracker):
        t, mgr = tracker
        t.kill_tree()
        assert mgr.terminated == 1

    def test_kill_tree_with_timeout(self, tracker):
        t, mgr = tracker
        t.kill_tree(timeout=1.5)
        assert mgr.terminated == 1

    def test_get_root_exit_code(self, tracker):
        t, mgr = tracker
        assert t.get_root_exit_code() is None  # root_exit=None 表示未退出
        mgr.root_exit = 4
        assert t.get_root_exit_code() == 4

    def test_get_process_exit_code(self, tracker):
        t, mgr = tracker
        assert t.get_process_exit_code(5) == 7

    def test_drain_notifications(self, tracker):
        t, mgr = tracker
        notifs = t.drain_notifications()
        assert len(notifs) == 1
        assert notifs[0].is_spawn()
        assert t.drain_notifications() == []

    def test_gui_methods_default_empty(self, tracker):
        # 沙箱无 GUI 窗口概念：保持基类默认空实现
        t, mgr = tracker
        assert t.get_gui_windows() == []
        assert t.poll_gui_windows() == []
        assert t.close_gui_window(0) is False

    def test_close_delegates(self, tracker):
        t, mgr = tracker
        t.close()
        assert mgr.closed is True
