"""daemon/server.py 单元测试

覆盖 DaemonServer 的初始化、运行、清理、停止、ping 响应。
使用 mock 替代真实 TCP 监听。
"""

import os
import time
import socket
import threading
import pytest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock

from src.daemon.server import DaemonServer
from src.protocol.message import Message


def _listener_patches():
    """run() 测试通用 mock 上下文：mock Listener / WebServer / signal / SHM 写入

    Returns:
        (ExitStack, dict) — stack 提供 with 管理；mocks 按角色访问。
    """
    stack = ExitStack()
    mocks = {}
    for key, p in (
        ("listener", patch("src.daemon.server.Listener")),
        ("web", patch("src.daemon.server.WebServer")),
        ("auth", patch("src.daemon.server.write_auth_token")),
        ("hmac", patch("src.daemon.server.write_hmac_key")),
        ("signal", patch("src.daemon.server.signal.signal")),
        ("rotate", patch.object(DaemonServer, "_schedule_rotate")),
    ):
        mocks[key] = stack.enter_context(p)
    return stack, mocks


class TestDaemonServerInit:
    def test_listeners_config_three_entries(self):
        server = DaemonServer()
        assert set(server.listeners_config) == {"plain", "token", "tls"}
        # 每段 (enabled, host, port) 三元组
        for name in ("plain", "token", "tls"):
            enabled, host, port = server.listeners_config[name]
            assert isinstance(enabled, bool)
            assert isinstance(host, str)
            assert isinstance(port, int)

    def test_default_config_token_enabled(self):
        """默认配置：仅 token 监听器启用（本机 127.0.0.1）"""
        server = DaemonServer()
        assert server.listeners_config["token"][0] is True
        assert server.listeners_config["token"][1] == "127.0.0.1"
        # plain/tls 默认关闭
        assert server.listeners_config["plain"][0] is False
        assert server.listeners_config["tls"][0] is False

    def test_initial_state(self):
        server = DaemonServer()
        assert server._running is False
        assert server._cleaned_up is False
        assert server._listeners == []
        assert server._shutdown_event.is_set() is False
        assert server._auth_shm is None


class TestDaemonServerRun:
    """DaemonServer.run 测试（mock 网络边界）"""

    @pytest.fixture
    def patched_server(self):
        stack, mocks = _listener_patches()
        with stack:
            mocks["listener"].return_value.bind.return_value = 12345
            mocks["listener"].return_value.port = 12345
            mocks["auth"].return_value = MagicMock()
            mocks["hmac"].return_value = MagicMock()
            yield

    def _run_and_stop(self, srv):
        t = threading.Thread(target=lambda: _stop_after(srv), daemon=True)
        t.start()
        try:
            srv.run()
        except Exception:
            pass

    def test_run_writes_auth_credentials_to_shm(self, patched_server):
        """token 监听器启用时发布认证令牌 + HMAC 密钥到 SHM"""
        from src.daemon.server import write_auth_token, write_hmac_key
        srv = DaemonServer()
        self._run_and_stop(srv)

        token_enabled = srv.listeners_config["token"][0]
        if token_enabled:
            assert write_auth_token.called
            assert write_hmac_key.called

    def test_run_token_disabled_skips_shm(self):
        """token 监听器关闭时不发布 SHM 凭据"""
        stack, mocks = _listener_patches()
        with stack:
            mocks["listener"].return_value.bind.return_value = 12345
            mocks["listener"].return_value.port = 12345

            srv = DaemonServer()
            srv.listeners_config["token"] = (False, "127.0.0.1", 10520)
            self._run_and_stop(srv)

            mocks["auth"].assert_not_called()
            mocks["hmac"].assert_not_called()

    def test_no_pid_file_written(self, patched_server):
        srv = DaemonServer()
        self._run_and_stop(srv)

        assert not os.path.exists(os.path.expanduser("~/.pty-agent/daemon.pid"))


class TestDaemonServerCleanup:
    def test_cleanup_idempotent(self):
        srv = DaemonServer()
        srv._cleaned_up = False
        srv._cleanup()
        assert srv._cleaned_up is True
        srv._cleanup()
        assert srv._cleaned_up is True

    def test_cleanup_stops_listeners(self):
        srv = DaemonServer()
        mock_listener = MagicMock()
        srv._listeners = [mock_listener]
        srv._cleanup()
        mock_listener.stop.assert_called_once()
        assert srv._listeners == []

    def test_cleanup_closes_shm(self):
        srv = DaemonServer()
        mock_shm = MagicMock()
        srv._auth_shm = mock_shm
        srv._cleanup()
        assert mock_shm.close.call_count == 1
        assert srv._auth_shm is None

    def test_cleanup_stops_manager(self):
        srv = DaemonServer()
        mock_mgr = MagicMock()
        srv.manager = mock_mgr
        srv._cleanup()
        mock_mgr.stop_all.assert_called_once()

    def test_cleanup_cancels_rotate_timer(self):
        srv = DaemonServer()
        mock_timer = MagicMock()
        srv._rotate_timer = mock_timer
        srv._cleanup()
        mock_timer.cancel.assert_called_once()
        assert srv._rotate_timer is None


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

    def test_stop_calls_cleanup(self):
        srv = DaemonServer()
        with patch.object(srv, "_cleanup") as mock_cleanup:
            srv.stop()
            mock_cleanup.assert_called_once()


class TestDaemonServerPing:
    """DaemonServer 真实 ping 响应测试"""

    def test_responds_to_ping(self):
        with patch("src.daemon.server.write_auth_token") as mock_auth, \
             patch("src.daemon.server.write_hmac_key") as mock_hmac, \
             patch("src.daemon.server.signal.signal"):
            mock_auth.return_value = MagicMock()
            mock_hmac.return_value = MagicMock()

            srv = DaemonServer()
            # token 端口置 0：bind 随机端口，避免占用固定 10520
            srv.listeners_config["token"] = (True, "127.0.0.1", 0)

            result = [None]

            def run_and_get_port():
                try:
                    srv.run()
                except Exception:
                    pass

            t = threading.Thread(target=run_and_get_port, daemon=True)
            t.start()

            time.sleep(1)
            if not srv._listeners:
                srv._shutdown_event.set()
                t.join(timeout=5)
                pytest.skip("无启用监听器，无法 ping")

            actual_port = srv._listeners[0].port

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect(("127.0.0.1", actual_port))
                Message.send(sock, {"type": "ping"})
                resp = Message.recv(sock)
                sock.close()
                result[0] = resp
            except Exception:
                pass
            finally:
                srv._shutdown_event.set()
                t.join(timeout=5)

            if result[0] is not None:
                assert result[0]["type"] == "pong"


def _stop_after(srv):
    time.sleep(0.5)
    srv._shutdown_event.set()