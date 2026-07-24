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

    def test_get_child_process_exit_code_returns_none(self):
        pty = PseudoTerminal()
        assert pty.get_child_process_exit_code(123) is None

    def test_get_job_notifications_returns_empty(self):
        pty = PseudoTerminal()
        assert pty.get_job_notifications() == []

    def test_get_process_list_returns_empty(self):
        pty = PseudoTerminal()
        assert pty.get_process_list() == []

    def test_get_gui_windows_returns_empty(self):
        pty = PseudoTerminal()
        assert pty.get_gui_windows() == []

    def test_poll_gui_windows_returns_empty(self):
        pty = PseudoTerminal()
        assert pty.poll_gui_windows() == []

    def test_close_gui_window_returns_false(self):
        pty = PseudoTerminal()
        assert pty.close_gui_window(0) is False

    def test_kill_tree_does_nothing(self):
        pty = PseudoTerminal()
        pty.kill_tree()
