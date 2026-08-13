"""process/base.py 单元测试 — ProcessNotification 与 ProcessTreeTracker 抽象端口"""

import pytest

from src.process.base import (
    ProcessNotification,
    ProcessTreeTracker,
    NOTIF_SPAWN,
    NOTIF_EXIT,
    NOTIF_CRASH,
)


class TestProcessNotification:
    def test_spawn(self):
        n = ProcessNotification(NOTIF_SPAWN, pid=123, process_name="cmd.exe")
        assert n.is_spawn() is True
        assert n.is_exit() is False
        assert n.is_crash() is False
        assert n.pid == 123
        assert n.exit_code is None
        assert n.process_name == "cmd.exe"

    def test_exit(self):
        n = ProcessNotification(NOTIF_EXIT, pid=123, exit_code=0)
        assert n.is_exit() is True
        assert n.is_spawn() is False
        assert n.is_crash() is False
        assert n.exit_code == 0

    def test_crash(self):
        n = ProcessNotification(NOTIF_CRASH, pid=123, exit_code=1)
        assert n.is_crash() is True
        assert n.is_exit() is False
        assert n.is_spawn() is False
        assert n.exit_code == 1

    def test_repr(self):
        n = ProcessNotification(NOTIF_EXIT, pid=456, exit_code=0)
        assert "EXIT" in repr(n).upper() or "exit" in repr(n)


class TestProcessTreeTracker:
    def test_abstract_cannot_instantiate(self):
        """抽象端口不允许直接实例化"""
        with pytest.raises(TypeError):
            ProcessTreeTracker()

    def test_gui_defaults(self):
        """GUI 三件套默认空实现，非 Windows 平台零负担"""
        class FakeTracker(ProcessTreeTracker):
            def register_root(self, pid, hprocess=None):
                return False

            def get_process_list(self):
                return []

            def is_root_alive(self):
                return False

            def kill_tree(self, timeout=3.0):
                pass

            def get_root_exit_code(self):
                return None

            def get_process_exit_code(self, pid):
                return None

            def drain_notifications(self):
                return []

            def close(self):
                pass

        t = FakeTracker()
        assert t.get_gui_windows() == []
        assert t.poll_gui_windows() == []
        assert t.close_gui_window(0) is False

    def test_get_process_count(self):
        """get_process_count 基于 get_process_list"""
        class FakeTracker(ProcessTreeTracker):
            def register_root(self, pid, hprocess=None):
                return True

            def get_process_list(self):
                return [100, 200, 300]

            def is_root_alive(self):
                return True

            def kill_tree(self, timeout=3.0):
                pass

            def get_root_exit_code(self):
                return None

            def get_process_exit_code(self, pid):
                return None

            def drain_notifications(self):
                return []

            def close(self):
                pass

        assert FakeTracker().get_process_count() == 3
