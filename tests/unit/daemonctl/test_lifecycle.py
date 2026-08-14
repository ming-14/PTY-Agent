"""守护进程生命周期管理单元测试

测试 pid_exists、_ping_daemon、_find_daemon_port、_find_daemon_pid、
is_running、start_daemon、stop_daemon 的各种路径。
使用 mock 替代 TCP 连接与单实例锁。
"""

import os
import sys
import time
import socket
import threading
import pytest
from unittest.mock import patch, MagicMock

from src.common.process import pid_exists
from src.daemonctl.lifecycle import (
    _ping_daemon,
    _find_daemon_port,
    _find_daemon_pid,
    is_running,
    start_daemon,
    stop_daemon,
)
from src.protocol.message import Message


class TestPidExists:
    """pid_exists 测试"""

    def test_current_pid_exists(self):
        assert pid_exists(os.getpid()) is True

    def test_nonexistent_pid(self):
        assert pid_exists(99999999) is False

    def test_init_pid_exists(self):
        if sys.platform == "win32":
            assert pid_exists(0) is False
        else:
            assert pid_exists(1) is True


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
    """_find_daemon_port 测试（固定端口模式）"""

    def test_returns_none_when_not_running(self):
        with patch("src.daemonctl.lifecycle.SingleInstanceLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = False
            mock_lock_cls.return_value = mock_lock
            assert _find_daemon_port() is None

    def test_returns_fixed_port_when_running(self):
        from src.config.client import TOKEN_PORT
        with patch("src.daemonctl.lifecycle.SingleInstanceLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = True
            mock_lock_cls.return_value = mock_lock
            assert _find_daemon_port() == TOKEN_PORT


class TestFindDaemonPid:
    """_find_daemon_pid 测试（经单实例锁持有者查询）"""

    def test_returns_none_when_not_running(self):
        with patch("src.daemonctl.lifecycle.SingleInstanceLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = False
            mock_lock_cls.return_value = mock_lock
            assert _find_daemon_pid() is None

    def test_returns_owner_pid_when_running(self):
        with patch("src.daemonctl.lifecycle.SingleInstanceLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = True
            mock_lock_cls.return_value = mock_lock
            mock_lock_cls.find_owner_pid.return_value = os.getpid()
            assert _find_daemon_pid() == os.getpid()


class TestIsRunning:
    """is_running 测试（单实例锁判断）"""

    def test_not_running_when_no_daemon(self):
        with patch("src.daemonctl.lifecycle.SingleInstanceLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = False
            mock_lock_cls.return_value = mock_lock
            assert is_running() is False

    def test_running_when_daemon_healthy(self):
        with patch("src.daemonctl.lifecycle.SingleInstanceLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = True
            mock_lock_cls.return_value = mock_lock
            assert is_running() is True


class TestStartDaemon:
    """start_daemon 测试（固定端口，不传 --port）"""

    def test_skips_when_already_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemonctl.lifecycle._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.daemonctl.lifecycle.SingleInstanceLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = True
            mock_lock_cls.return_value = mock_lock
            start_daemon()
        assert any("already running" in p.lower() or "已在运行" in p for p in printed)

    def test_starts_new_daemon_when_not_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemonctl.lifecycle._safe_print",
            lambda s: printed.append(s),
        )

        with patch("src.daemonctl.lifecycle.SingleInstanceLock") as mock_lock_cls, \
             patch("src.daemonctl.lifecycle.subprocess.Popen") as mock_popen:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = False
            mock_lock_cls.return_value = mock_lock
            mock_proc = MagicMock()
            mock_proc.pid = 1234
            mock_popen.return_value = mock_proc

            with patch("src.daemonctl.lifecycle.is_running", return_value=True):
                start_daemon()

            assert mock_popen.called
            # 固定端口模式：不带 --port 参数
            args = mock_popen.call_args[0][0]
            assert "--port" not in args


class TestStopDaemon:
    """stop_daemon 测试"""

    def test_not_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemonctl.lifecycle._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.daemonctl.lifecycle._find_daemon_port", return_value=None), \
             patch("src.daemonctl.lifecycle.SingleInstanceLock") as mock_lock_cls, \
             patch("src.daemonctl.lifecycle._cleanup_credentials"):
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = False
            mock_lock_cls.return_value = mock_lock
            stop_daemon()
        assert any("not running" in p.lower() or "未运行" in p for p in printed)

    def test_stop_via_tcp(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemonctl.lifecycle._safe_print",
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
                Message.send(conn, {"commandType": "stop", "code": 0, "msg": "ok"})
            conn.close()

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        with patch("src.daemonctl.lifecycle._find_daemon_port", return_value=port), \
             patch("src.daemonctl.lifecycle._find_daemon_pid", return_value=os.getpid()), \
             patch("src.daemonctl.lifecycle.read_auth_token", return_value="test"), \
             patch("src.daemonctl.lifecycle._cleanup_credentials"):
            stop_daemon()

        assert any("stopped" in p.lower() or "已停止" in p for p in printed)
        srv.close()
        t.join(timeout=3)

    def test_stop_force_kill_when_tcp_fails(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemonctl.lifecycle._safe_print",
            lambda s: printed.append(s),
        )

        with patch("src.daemonctl.lifecycle._find_daemon_port", return_value=19999), \
             patch("src.daemonctl.lifecycle._find_daemon_pid", return_value=99999999), \
             patch("src.daemonctl.lifecycle._cleanup_credentials"):
            stop_daemon()
        # PID 不存在，无法 kill，但不应崩溃