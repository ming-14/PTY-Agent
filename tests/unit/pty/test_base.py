"""pty/base.py 单元测试"""

import pytest

from src.pty.base import PseudoTerminal


class TestPseudoTerminalBase:
    def test_get_type(self):
        pty = PseudoTerminal()
        assert pty.get_type() == "unknown"

    def test_read_raises(self):
        pty = PseudoTerminal()
        with pytest.raises(NotImplementedError):
            pty.read()

    def test_write_raises(self):
        pty = PseudoTerminal()
        with pytest.raises(NotImplementedError):
            pty.write(b"data")

    def test_close_raises(self):
        pty = PseudoTerminal()
        with pytest.raises(NotImplementedError):
            pty.close()

    def test_drain_returns_empty(self):
        pty = PseudoTerminal()
        assert pty.drain() == b""

    def test_resize_does_nothing(self):
        pty = PseudoTerminal()
        pty.resize(120, 40)

    def test_fileno_returns_none(self):
        pty = PseudoTerminal()
        assert pty.fileno() is None

    def test_get_child_pid_returns_none(self):
        pty = PseudoTerminal()
        assert pty.get_child_pid() is None

    def test_get_exit_code_returns_none(self):
        pty = PseudoTerminal()
        assert pty.get_exit_code() is None

    def test_inject_mouse_event_returns_false(self):
        pty = PseudoTerminal()
        assert pty.inject_mouse_event(0, 0, 0, False) is False

    def test_no_process_management_methods(self):
        """进程管理方法（kill_tree/进程列表/通知/GUI）已迁出到 process/tracker"""
        pty = PseudoTerminal()
        for name in ("kill_tree", "get_process_list", "get_child_process_exit_code",
                     "get_job_notifications", "get_gui_windows",
                     "poll_gui_windows", "close_gui_window"):
            assert not hasattr(pty, name), f"PseudoTerminal 不应再暴露 {name}"
