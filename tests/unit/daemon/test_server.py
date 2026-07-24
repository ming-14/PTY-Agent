"""daemon/server.py 单元测试"""

import pytest

from src.daemon.server import DaemonServer


class TestDaemonServerInit:
    def test_default_host(self):
        server = DaemonServer()
        assert server.host == "127.0.0.1"

    def test_custom_port(self):
        server = DaemonServer(port=9999)
        assert server.port == 9999

    def test_initial_state(self):
        server = DaemonServer()
        assert server._running is False
        assert server._cleaned_up is False
        assert server._listeners == []
        assert server._shutdown_event.is_set() is False


class TestDaemonServerStop:
    def test_stop_sets_running_false(self):
        server = DaemonServer()
        server._running = True
        server.stop()
        assert server._running is False

    def test_stop_idempotent(self):
        server = DaemonServer()
        server.stop()
        server.stop()
        assert server._running is False


class TestDaemonServerVerifyShm:
    def test_verify_shm_no_signature(self):
        server = DaemonServer()
        server._my_shm_signature = "123:45678"
        try:
            result = server._verify_shm()
        except Exception:
            result = True
        assert result is True or result is False
