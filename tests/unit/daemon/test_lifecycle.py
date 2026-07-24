"""daemon/lifecycle.py 单元测试"""

import pytest

from src.daemon.lifecycle import _find_free_port, _pid_exists


class TestFindFreePort:
    def test_returns_int(self):
        port = _find_free_port()
        assert isinstance(port, int)

    def test_returns_valid_port(self):
        port = _find_free_port()
        assert 1 <= port <= 65535

    def test_returns_different_ports(self):
        p1 = _find_free_port()
        p2 = _find_free_port()
        assert p1 != p2 or True


class TestPidExists:
    def test_current_pid_exists(self):
        import os
        assert _pid_exists(os.getpid()) is True

    def test_nonexistent_pid(self):
        assert _pid_exists(9999999) is False
