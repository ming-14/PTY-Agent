"""pty/unix/process.py 单元测试 — UnixNotification 类"""

import pytest

from src.pty.unix.process import UnixNotification


class TestUnixNotification:
    def test_is_spawn(self):
        n = UnixNotification("process_spawn", pid=123)
        assert n.is_spawn() is True
        assert n.is_exit() is False
        assert n.is_crash() is False

    def test_is_exit(self):
        n = UnixNotification("process_exit", pid=123, exit_code=0)
        assert n.is_exit() is True
        assert n.is_spawn() is False
        assert n.is_crash() is False

    def test_is_crash(self):
        n = UnixNotification("process_crash", pid=123, exit_code=1)
        assert n.is_crash() is True
        assert n.is_exit() is False
        assert n.is_spawn() is False

    def test_properties(self):
        n = UnixNotification("process_spawn", pid=456)
        assert n.type == "process_spawn"
        assert n.pid == 456
        assert n.exit_code is None

    def test_repr(self):
        n = UnixNotification("process_exit", pid=789, exit_code=0)
        r = repr(n)
        assert "process_exit" in r
        assert "789" in r
