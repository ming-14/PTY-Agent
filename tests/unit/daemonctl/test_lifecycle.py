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
from src.client.daemonctl import (
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
        with patch("src.client.daemonctl.SingleInstanceLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = False
            mock_lock_cls.return_value = mock_lock
            assert _find_daemon_port() is None

    def test_returns_fixed_port_when_running(self):
        from src.config.client import TOKEN_PORT
        with patch("src.client.daemonctl.SingleInstanceLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = True
            mock_lock_cls.return_value = mock_lock
            assert _find_daemon_port() == TOKEN_PORT


class TestFindDaemonPid:
    """_find_daemon_pid 测试（锁持有者查询优先，端口回退兜底）"""

    def test_returns_none_when_not_running(self):
        """锁无持有者且无监听端口 → None"""
        with patch("src.client.daemonctl.SingleInstanceLock") as mock_lock_cls:
            mock_lock_cls.find_owner_pid.return_value = None
            with patch("src.client.daemonctl._find_daemon_port", return_value=None):
                assert _find_daemon_pid() is None

    def test_returns_owner_pid_when_running(self):
        """锁有持有者 → 返回持有者 PID（不走端口回退）"""
        with patch("src.client.daemonctl.SingleInstanceLock") as mock_lock_cls:
            mock_lock_cls.find_owner_pid.return_value = os.getpid()
            assert _find_daemon_pid() == os.getpid()

    def test_falls_back_to_port_when_lock_missing(self):
        """锁无持有者但端口在监听 → 端口级回退找到 PID

        端口回退经 /proc/net/tcp 实现（_find_pid_by_port_unix），仅 Unix 有；
        Windows 上 _find_daemon_pid 无端口回退路径（IS_WINDOWS 直接 return None）。
        """
        if sys.platform == "win32":
            pytest.skip("端口级 PID 回退仅 Unix 实现（/proc/net/tcp）")
        with patch("src.client.daemonctl.SingleInstanceLock") as mock_lock_cls:
            mock_lock_cls.find_owner_pid.return_value = None
            with patch("src.client.daemonctl._find_daemon_port", return_value=10520):
                with patch("src.client.daemonctl._find_pid_by_port_unix", return_value=12345):
                    assert _find_daemon_pid() == 12345


class TestIsRunning:
    """is_running 测试（单实例锁判断）"""

    def test_not_running_when_no_daemon(self):
        with patch("src.client.daemonctl.SingleInstanceLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = False
            mock_lock_cls.return_value = mock_lock
            assert is_running() is False

    def test_running_when_daemon_healthy(self):
        with patch("src.client.daemonctl.SingleInstanceLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = True
            mock_lock_cls.return_value = mock_lock
            assert is_running() is True


class TestStartDaemon:
    """start_daemon 测试（固定端口，不传 --port）"""

    def test_skips_when_already_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.daemonctl._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.client.daemonctl.SingleInstanceLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = True
            mock_lock_cls.return_value = mock_lock
            start_daemon()
        assert any("already running" in p.lower() or "已在运行" in p for p in printed)

    def test_starts_new_daemon_when_not_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.daemonctl._safe_print",
            lambda s: printed.append(s),
        )

        # Unix 走 fork+execve，Windows 走 Popen：按平台 mock 各自入口
        is_unix = hasattr(os, "fork")
        mock_fork = None
        mock_execve = None
        if is_unix:
            mock_fork = patch("src.client.daemonctl.os.fork",
                              return_value=0).start()
            mock_execve = patch("src.client.daemonctl.os.execve").start()
            patch("src.client.daemonctl.os._exit").start()
            # fork mock 为 0 会让测试进程自身走 daemon 化路径的
            # os.chdir("/")（真实子进程才该执行），会污染 pytest 的 cwd，
            # 导致后续相对路径子进程（如 node 脚本测试）找不到文件。
            patch("src.client.daemonctl.os.chdir").start()
        try:
            with patch("src.client.daemonctl.SingleInstanceLock") as mock_lock_cls, \
                 patch("src.client.daemonctl.subprocess.Popen") as mock_popen:
                mock_lock = MagicMock()
                mock_lock.is_locked.return_value = False
                mock_lock_cls.return_value = mock_lock
                mock_proc = MagicMock()
                mock_proc.pid = 1234
                mock_popen.return_value = mock_proc

                with patch("src.client.daemonctl.is_running", return_value=True):
                    start_daemon()

                # Windows 走 Popen；Unix 走 fork+execve，两者其一被调用
                assert mock_popen.called or (is_unix and mock_execve.called)
        finally:
            patch.stopall()


class TestStopDaemon:
    """stop_daemon 测试"""

    def test_not_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.daemonctl._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.client.daemonctl._find_daemon_port", return_value=None), \
             patch("src.client.daemonctl.SingleInstanceLock") as mock_lock_cls, \
             patch("src.client.daemonctl._cleanup_credentials"):
            mock_lock = MagicMock()
            mock_lock.is_locked.return_value = False
            mock_lock_cls.return_value = mock_lock
            stop_daemon()
        assert any("not running" in p.lower() or "未运行" in p for p in printed)

    def test_stop_via_tcp(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.daemonctl._safe_print",
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

        with patch("src.client.daemonctl._find_daemon_port", return_value=port), \
             patch("src.client.daemonctl._find_daemon_pid", return_value=os.getpid()), \
             patch("src.client.daemonctl._cleanup_credentials"):
            stop_daemon()

        assert any("stopped" in p.lower() or "已停止" in p for p in printed)
        srv.close()
        t.join(timeout=3)

    def test_stop_via_tcp_envelope_response(self, monkeypatch):
        """daemon 响应为信封形态（commandType 在 payload）时也能判定成功"""
        printed = []
        monkeypatch.setattr(
            "src.client.daemonctl._safe_print",
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
                Message.send(
                    conn,
                    {"dir": "response", "type": "stop", "payload": {
                        "commandType": "stop", "code": 0, "msg": "ok"}},
                )
            conn.close()

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        with patch("src.client.daemonctl._find_daemon_port", return_value=port), \
             patch("src.client.daemonctl._find_daemon_pid", return_value=os.getpid()), \
             patch("src.client.daemonctl._cleanup_credentials"):
            stop_daemon()

        assert any("stopped" in p.lower() or "已停止" in p for p in printed)
        srv.close()
        t.join(timeout=3)

    def test_stop_failure_keeps_shm_credentials(self, monkeypatch):
        """stop 失败（daemon 仍存活）时不得清理凭据 SHM，保证后续重试可认证"""
        printed = []
        monkeypatch.setattr(
            "src.client.daemonctl._safe_print",
            lambda s: printed.append(s),
        )

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def handle():
            conn, _ = srv.accept()
            Message.recv(conn)
            Message.send(conn, {"dir": "response", "type": "error",
                                "payload": {"type": "error",
                                            "message": "Authentication failed"}})
            conn.close()

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        cleanup = MagicMock()
        with patch("src.client.daemonctl._find_daemon_port", return_value=port), \
             patch("src.client.daemonctl._cleanup_credentials", cleanup):
            stop_daemon()

        cleanup.assert_not_called()
        assert any("failed" in p.lower() or "失败" in p for p in printed)
        srv.close()
        t.join(timeout=3)

    def test_stop_force_kill_when_tcp_fails(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.daemonctl._safe_print",
            lambda s: printed.append(s),
        )

        with patch("src.client.daemonctl._find_daemon_port", return_value=19999), \
             patch("src.client.daemonctl._find_daemon_pid", return_value=99999999), \
             patch("src.client.daemonctl._cleanup_credentials"):
            stop_daemon()
        # PID 不存在，无法 kill，但不应崩溃