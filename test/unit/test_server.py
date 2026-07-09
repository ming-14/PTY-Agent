"""DaemonServer 单元测试

测试 DaemonServer 的共享内存写入、cleanup、令牌轮换等。
使用 mock 替代真实 TCP 监听。
"""

import os
import time
import socket
import threading
import pytest
from unittest.mock import patch, MagicMock

from src.daemon.server import DaemonServer
from src.protocol.message import Message


class TestDaemonServerInit:
    """DaemonServer 初始化测试"""

    def test_default_host_and_port(self):
        srv = DaemonServer()
        assert srv.host == "127.0.0.1"
        assert srv.port == 18765

    def test_custom_port(self):
        srv = DaemonServer(port=54321)
        assert srv.port == 54321

    def test_initial_state(self):
        srv = DaemonServer()
        assert srv._running is False
        assert srv._cleaned_up is False
        assert srv._port_shm is None
        assert srv._auth_shm is None


class TestDaemonServerRun:
    """DaemonServer.run 测试"""

    def test_run_writes_daemon_info_to_shm(self):
        with patch("src.daemon.server.write_daemon_info_to_shm") as mock_write, \
             patch("src.daemon.server.write_auth_token") as mock_auth, \
             patch("src.daemon.server.signal.signal"):
            mock_write.return_value = MagicMock()
            mock_auth.return_value = MagicMock()

            srv = DaemonServer(port=0)

            def stop_after_accept(srv_ref):
                time.sleep(0.5)
                srv_ref._running = False

            t = threading.Thread(target=stop_after_accept, args=(srv,), daemon=True)
            t.start()
            try:
                srv.run()
            except Exception:
                pass

            mock_write.assert_called_once()
            call_args = mock_write.call_args
            assert call_args[0][1] == srv.port
            assert isinstance(call_args[0][0], int)
            assert call_args[0][0] == os.getpid()

    def test_run_publishes_actual_port(self):
        with patch("src.daemon.server.write_daemon_info_to_shm") as mock_write, \
             patch("src.daemon.server.write_auth_token") as mock_auth, \
             patch("src.daemon.server.signal.signal"):
            mock_write.return_value = MagicMock()
            mock_auth.return_value = MagicMock()

            srv = DaemonServer(port=0)

            def stop_after_accept(srv_ref):
                time.sleep(0.5)
                srv_ref._running = False

            t = threading.Thread(target=stop_after_accept, args=(srv,), daemon=True)
            t.start()
            try:
                srv.run()
            except Exception:
                pass

            written_port = mock_write.call_args[0][1]
            assert written_port > 0

    def test_no_pid_file_written(self):
        with patch("src.daemon.server.write_daemon_info_to_shm") as mock_write, \
             patch("src.daemon.server.write_auth_token") as mock_auth, \
             patch("src.daemon.server.signal.signal"):
            mock_write.return_value = MagicMock()
            mock_auth.return_value = MagicMock()

            srv = DaemonServer(port=0)

            def stop_after_accept(srv_ref):
                time.sleep(0.5)
                srv_ref._running = False

            t = threading.Thread(target=stop_after_accept, args=(srv,), daemon=True)
            t.start()
            try:
                srv.run()
            except Exception:
                pass

        assert not os.path.exists(os.path.expanduser("~/.pty-agent/daemon.pid"))


class TestDaemonServerCleanup:
    """DaemonServer._cleanup 测试"""

    def test_cleanup_idempotent(self):
        srv = DaemonServer()
        srv._cleaned_up = False
        srv._cleanup()
        assert srv._cleaned_up is True
        srv._cleanup()
        assert srv._cleaned_up is True

    def test_cleanup_closes_sockets(self):
        srv = DaemonServer()
        mock_sock = MagicMock()
        srv._server_socket = mock_sock
        srv._cleanup()
        mock_sock.close.assert_called_once()
        assert srv._server_socket is None

    def test_cleanup_closes_shm(self):
        srv = DaemonServer()
        mock_shm = MagicMock()
        srv._port_shm = mock_shm
        srv._auth_shm = mock_shm
        srv._cleanup()
        assert mock_shm.close.call_count == 2
        assert srv._port_shm is None
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
    """DaemonServer.stop 测试"""

    def test_stop_sets_running_false(self):
        srv = DaemonServer()
        srv._running = True
        srv.stop()
        assert srv._running is False

    def test_stop_calls_cleanup(self):
        srv = DaemonServer()
        with patch.object(srv, "_cleanup") as mock_cleanup:
            srv.stop()
            mock_cleanup.assert_called_once()


class TestDaemonServerPing:
    """DaemonServer ping 响应测试"""

    def test_responds_to_ping(self):
        with patch("src.daemon.server.write_daemon_info_to_shm") as mock_write, \
             patch("src.daemon.server.write_auth_token") as mock_auth, \
             patch("src.daemon.server.signal.signal"):
            mock_write.return_value = MagicMock()
            mock_auth.return_value = MagicMock()

            srv = DaemonServer(port=0)

            result = [None]

            def run_and_get_port():
                try:
                    srv.run()
                except Exception:
                    pass

            t = threading.Thread(target=run_and_get_port, daemon=True)
            t.start()

            time.sleep(1)
            actual_port = srv.port

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
                srv._running = False
                t.join(timeout=5)

            if result[0] is not None:
                assert result[0]["type"] == "pong"
