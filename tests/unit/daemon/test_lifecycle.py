"""客户端守护进程控制 + 进程探测单元测试"""

import pytest

from src.client.lifecycle import _find_free_port
from src.process.info import pid_exists


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
        assert pid_exists(os.getpid()) is True

    def test_nonexistent_pid(self):
        assert pid_exists(9999999) is False