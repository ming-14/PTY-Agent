"""守护进程生命周期管理单元测试

测试 _pid_exists、_ping_daemon、_find_daemon_port、_find_daemon_pid、
is_running、start_daemon、stop_daemon 的各种路径。
使用 mock 替代 TCP 连接和共享内存。
"""

import os
import sys
import time
import socket
import threading
import pytest
from unittest.mock import patch, MagicMock

from src.daemon.lifecycle import (
    _pid_exists,
    _ping_daemon,
    _find_daemon_port,
    _find_daemon_pid,
    is_running,
    start_daemon,
    stop_daemon,
)
from src.protocol.message import Message


class TestPidExists:
    """_pid_exists 测试"""

    def test_current_pid_exists(self):
        assert _pid_exists(os.getpid()) is True

    def test_nonexistent_pid(self):
        assert _pid_exists(99999999) is False

    def test_init_pid_exists(self):
        if sys.platform == "win32":
            assert _pid_exists(0) is False
        else:
            assert _pid_exists(1) is True


class TestPingDaemon:
    """_ping_daemon 测试"""

    def test_ping_dead_port(self):
        assert _ping_daemon(19999) is False

    def test_ping_real_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def handle():
            conn, _ = srv.accept()
            msg = Message.recv(conn)
            if msg and msg.get("type") == "ping":
                Message.send(conn, {"type": "pong"})
            conn.close()

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        assert _ping_daemon(port) is True
        srv.close()
        t.join(timeout=3)

    def test_ping_server_wrong_response(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def handle():
            conn, _ = srv.accept()
            msg = Message.recv(conn)
            Message.send(conn, {"type": "not_pong"})
            conn.close()

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        assert _ping_daemon(port) is False
        srv.close()
        t.join(timeout=3)


class TestFindDaemonPort:
    """_find_daemon_port 测试"""

    def test_returns_none_when_shm_empty(self):
        with patch("src.daemon.lifecycle.read_daemon_info_from_shm", return_value=None):
            assert _find_daemon_port() is None

    def test_returns_none_when_pid_dead(self):
        with patch("src.daemon.lifecycle.read_daemon_info_from_shm", return_value=(99999999, 12345)), \
             patch("src.daemon.lifecycle._cleanup_port"):
            assert _find_daemon_port() is None

    def test_returns_none_when_pid_alive_but_ping_fails(self):
        with patch("src.daemon.lifecycle.read_daemon_info_from_shm", return_value=(os.getpid(), 19999)), \
             patch("src.daemon.lifecycle._cleanup_port"):
            assert _find_daemon_port() is None

    def test_returns_port_when_daemon_healthy(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def handle():
            conn, _ = srv.accept()
            msg = Message.recv(conn)
            if msg and msg.get("type") == "ping":
                Message.send(conn, {"type": "pong"})
            conn.close()

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        with patch("src.daemon.lifecycle.read_daemon_info_from_shm", return_value=(os.getpid(), port)):
            result = _find_daemon_port()
            assert result == port

        srv.close()
        t.join(timeout=3)

    def test_cleans_up_shm_when_pid_dead(self):
        with patch("src.daemon.lifecycle.read_daemon_info_from_shm", return_value=(99999999, 12345)):
            with patch("src.daemon.lifecycle._cleanup_port") as mock_cleanup:
                _find_daemon_port()
                mock_cleanup.assert_called_once()

    def test_cleans_up_shm_when_ping_fails(self):
        with patch("src.daemon.lifecycle.read_daemon_info_from_shm", return_value=(os.getpid(), 19999)):
            with patch("src.daemon.lifecycle._cleanup_port") as mock_cleanup:
                _find_daemon_port()
                mock_cleanup.assert_called_once()


class TestFindDaemonPid:
    """_find_daemon_pid 测试"""

    def test_returns_none_when_shm_empty(self):
        with patch("src.daemon.lifecycle.read_daemon_info_from_shm", return_value=None):
            assert _find_daemon_pid() is None

    def test_returns_none_when_pid_dead(self):
        with patch("src.daemon.lifecycle.read_daemon_info_from_shm", return_value=(99999999, 12345)), \
             patch("src.daemon.lifecycle._cleanup_port"):
            assert _find_daemon_pid() is None

    def test_returns_pid_when_daemon_healthy(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def handle():
            conn, _ = srv.accept()
            msg = Message.recv(conn)
            if msg and msg.get("type") == "ping":
                Message.send(conn, {"type": "pong"})
            conn.close()

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        with patch("src.daemon.lifecycle.read_daemon_info_from_shm", return_value=(os.getpid(), port)):
            result = _find_daemon_pid()
            assert result == os.getpid()

        srv.close()
        t.join(timeout=3)


class TestIsRunning:
    """is_running 测试"""

    def test_not_running_when_no_daemon(self):
        with patch("src.daemon.lifecycle._find_daemon_port", return_value=None), \
             patch("src.daemon.lifecycle._cleanup_port"):
            assert is_running() is False

    def test_running_when_daemon_healthy(self):
        with patch("src.daemon.lifecycle._find_daemon_port", return_value=12345):
            assert is_running() is True

    def test_cleans_up_when_not_running(self):
        with patch("src.daemon.lifecycle._find_daemon_port", return_value=None):
            with patch("src.daemon.lifecycle._cleanup_port") as mock_cleanup:
                is_running()
                mock_cleanup.assert_called_once()


class TestStartDaemon:
    """start_daemon 测试"""

    def test_skips_when_already_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemon.lifecycle._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.daemon.lifecycle._find_daemon_port", return_value=12345):
            start_daemon()
        assert any("已在运行中" in p for p in printed)

    def test_starts_new_daemon_when_not_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemon.lifecycle._safe_print",
            lambda s: printed.append(s),
        )
        call_count = {"n": 0}

        def mock_find_port():
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return None
            return 54321

        with patch("src.daemon.lifecycle._find_daemon_port", side_effect=mock_find_port), \
             patch("src.daemon.lifecycle._find_free_port", return_value=54321), \
             patch("src.daemon.lifecycle.subprocess.Popen") as mock_popen, \
             patch("src.daemon.lifecycle.os.makedirs"):
            mock_proc = MagicMock()
            mock_proc.pid = 1234
            mock_popen.return_value = mock_proc

            with patch("src.daemon.lifecycle.read_port_from_shm", return_value=54321):
                start_daemon()

            assert mock_popen.called


class TestStopDaemon:
    """stop_daemon 测试"""

    def test_not_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemon.lifecycle._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.daemon.lifecycle._find_daemon_port", return_value=None), \
             patch("src.daemon.lifecycle._cleanup_port"):
            stop_daemon()
        assert any("未运行" in p for p in printed)

    def test_stop_via_tcp(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemon.lifecycle._safe_print",
            lambda s: printed.append(s),
        )

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def handle():
            conn, _ = srv.accept()
            msg = Message.recv(conn)
            if msg and msg.get("type") == "stop":
                Message.send(conn, {"type": "ok"})
            conn.close()

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        with patch("src.daemon.lifecycle._find_daemon_port", return_value=port), \
             patch("src.daemon.lifecycle._find_daemon_pid", return_value=os.getpid()), \
             patch("src.daemon.lifecycle.read_auth_token", return_value="test"), \
             patch("src.daemon.lifecycle._cleanup_port"):
            stop_daemon()

        assert any("已停止" in p for p in printed)
        srv.close()
        t.join(timeout=3)

    def test_stop_force_kill_when_tcp_fails(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemon.lifecycle._safe_print",
            lambda s: printed.append(s),
        )

        with patch("src.daemon.lifecycle._find_daemon_port", return_value=19999), \
             patch("src.daemon.lifecycle._find_daemon_pid", return_value=99999999), \
             patch("src.daemon.lifecycle._cleanup_port"):
            stop_daemon()
        # PID 不存在，无法 kill，但不应崩溃
