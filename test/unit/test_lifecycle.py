"""守护进程生命周期管理单元测试

测试 _pid_exists、_heartbeat_fresh、_find_daemon_pid、is_running、
start_daemon、stop_daemon 的各种路径。
使用 mock 替代共享内存与子进程操作（无 socket、无端口）。
"""

import os
import sys
import time
import pytest
from unittest.mock import patch, MagicMock

from src.daemon.lifecycle import (
    _pid_exists,
    _heartbeat_fresh,
    _find_daemon_pid,
    is_running,
    start_daemon,
    stop_daemon,
    _stop_via_mailbox,
)


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


class TestHeartbeatFresh:
    """_heartbeat_fresh 测试"""

    def test_fresh_heartbeat(self):
        assert _heartbeat_fresh(time.time()) is True

    def test_old_heartbeat(self):
        assert _heartbeat_fresh(time.time() - 60) is False

    def test_exactly_at_threshold(self):
        # 恰好等于阈值应视为新鲜（<= 判断）
        assert _heartbeat_fresh(time.time()) is True


class TestFindDaemonPid:
    """_find_daemon_pid 测试"""

    def test_returns_none_when_shm_empty(self):
        with patch("src.daemon.lifecycle.read_daemon_info", return_value=None):
            assert _find_daemon_pid() is None

    def test_returns_none_when_not_running(self):
        with patch("src.daemon.lifecycle.read_daemon_info",
                   return_value=(os.getpid(), False, time.time())), \
             patch("src.daemon.lifecycle._cleanup_shm_resources"):
            assert _find_daemon_pid() is None

    def test_returns_none_when_pid_dead(self):
        with patch("src.daemon.lifecycle.read_daemon_info",
                   return_value=(99999999, True, time.time())), \
             patch("src.daemon.lifecycle._cleanup_shm_resources"):
            assert _find_daemon_pid() is None

    def test_returns_none_when_heartbeat_stale(self):
        with patch("src.daemon.lifecycle.read_daemon_info",
                   return_value=(os.getpid(), True, time.time() - 60)), \
             patch("src.daemon.lifecycle._cleanup_shm_resources"):
            assert _find_daemon_pid() is None

    def test_returns_pid_when_healthy(self):
        with patch("src.daemon.lifecycle.read_daemon_info",
                   return_value=(os.getpid(), True, time.time())):
            assert _find_daemon_pid() == os.getpid()

    def test_cleans_up_when_pid_dead(self):
        with patch("src.daemon.lifecycle.read_daemon_info",
                   return_value=(99999999, True, time.time())):
            with patch("src.daemon.lifecycle._cleanup_shm_resources") as mock_cleanup:
                _find_daemon_pid()
                mock_cleanup.assert_called_once()


class TestIsRunning:
    """is_running 测试"""

    def test_not_running_when_no_daemon(self):
        with patch("src.daemon.lifecycle._find_daemon_pid", return_value=None):
            assert is_running() is False

    def test_running_when_daemon_healthy(self):
        with patch("src.daemon.lifecycle._find_daemon_pid", return_value=os.getpid()):
            assert is_running() is True


class TestStartDaemon:
    """start_daemon 测试"""

    def test_skips_when_already_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemon.lifecycle._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.daemon.lifecycle.is_running", return_value=True):
            start_daemon()
        assert any("已在运行中" in p for p in printed)

    def test_starts_new_daemon_when_not_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemon.lifecycle._safe_print",
            lambda s: printed.append(s),
        )
        call_count = {"n": 0}

        def mock_is_running():
            call_count["n"] += 1
            # 首次检查未运行，之后启动成功
            return call_count["n"] >= 2

        # 模拟 open(daemon.log)：返回可用的上下文管理器
        fake_file = MagicMock()
        fake_file.__enter__ = MagicMock(return_value=fake_file)
        fake_file.__exit__ = MagicMock(return_value=False)

        with patch("src.daemon.lifecycle.is_running", side_effect=mock_is_running), \
             patch("src.daemon.lifecycle.subprocess.Popen") as mock_popen, \
             patch("src.daemon.lifecycle.os.makedirs"), \
             patch("src.daemon.lifecycle.time.sleep"), \
             patch("builtins.open", return_value=fake_file):
            mock_proc = MagicMock()
            mock_proc.pid = 1234
            mock_popen.return_value = mock_proc
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
        with patch("src.daemon.lifecycle._find_daemon_pid", return_value=None), \
             patch("src.daemon.lifecycle._cleanup_shm_resources"):
            stop_daemon()
        assert any("未运行" in p for p in printed)

    def test_stop_via_mailbox(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemon.lifecycle._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.daemon.lifecycle._find_daemon_pid", return_value=os.getpid()), \
             patch("src.daemon.lifecycle._stop_via_mailbox", return_value=True), \
             patch("src.daemon.lifecycle._cleanup_shm_resources"):
            stop_daemon()
        assert any("已停止" in p for p in printed)

    def test_stop_force_kill_when_mailbox_fails(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemon.lifecycle._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.daemon.lifecycle._find_daemon_pid", return_value=99999999), \
             patch("src.daemon.lifecycle._stop_via_mailbox", return_value=False), \
             patch("src.daemon.lifecycle._pid_exists", return_value=False), \
             patch("src.daemon.lifecycle._cleanup_shm_resources"):
            stop_daemon()
        # PID 不存在，无法 kill，但不应崩溃，也不应打印"已停止"
        assert not any("已停止" in p for p in printed)

    def test_stop_force_kill_when_mailbox_fails_and_pid_alive(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.daemon.lifecycle._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.daemon.lifecycle._find_daemon_pid", return_value=os.getpid()), \
             patch("src.daemon.lifecycle._stop_via_mailbox", return_value=False), \
             patch("src.daemon.lifecycle._pid_exists", return_value=True), \
             patch("src.daemon.lifecycle.os.system") as mock_system, \
             patch("src.daemon.lifecycle._cleanup_shm_resources"):
            stop_daemon()
        mock_system.assert_called_once()
        assert any("已停止" in p for p in printed)


class TestStopViaMailbox:
    """_stop_via_mailbox 测试"""

    def _fake_shm(self):
        shm = MagicMock()
        shm.size.return_value = 1024 * 1024
        return shm

    def test_success_path(self):
        with patch("src.daemon.lifecycle.open_shm",
                   return_value=self._fake_shm()), \
             patch("src.daemon.lifecycle.read_auth_token", return_value="tok"), \
             patch("src.daemon.lifecycle.Mailbox") as mock_mailbox_cls:
            mock_mailbox = MagicMock()
            mock_mailbox.acquire_slot.return_value = 0
            mock_mailbox.wait_done.return_value = True
            mock_mailbox_cls.return_value = mock_mailbox
            with patch("src.daemon.lifecycle.read_message", return_value={"type": "ok"}):
                assert _stop_via_mailbox() is True

    def test_timeout_path(self):
        with patch("src.daemon.lifecycle.open_shm",
                   return_value=self._fake_shm()), \
             patch("src.daemon.lifecycle.read_auth_token", return_value="tok"), \
             patch("src.daemon.lifecycle.Mailbox") as mock_mailbox_cls:
            mock_mailbox = MagicMock()
            mock_mailbox.acquire_slot.return_value = 0
            mock_mailbox.wait_done.return_value = False
            mock_mailbox_cls.return_value = mock_mailbox
            assert _stop_via_mailbox() is False

    def test_mailbox_full_path(self):
        with patch("src.daemon.lifecycle.open_shm",
                   return_value=self._fake_shm()), \
             patch("src.daemon.lifecycle.read_auth_token", return_value="tok"), \
             patch("src.daemon.lifecycle.Mailbox") as mock_mailbox_cls:
            mock_mailbox = MagicMock()
            mock_mailbox.acquire_slot.return_value = None
            mock_mailbox_cls.return_value = mock_mailbox
            assert _stop_via_mailbox() is False

    def test_shm_unavailable_path(self):
        with patch("src.daemon.lifecycle.open_shm", return_value=None), \
             patch("src.daemon.lifecycle.read_auth_token", return_value="tok"):
            assert _stop_via_mailbox() is False