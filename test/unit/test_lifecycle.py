"""守护进程生命周期与管控单元测试

覆盖：
- protocol.daemon_utils：pid_exists / heartbeat_fresh / find_daemon_pid /
  cleanup_shm_resources（守护进程存活检测，client 与 daemon 共用）
- client.controller：is_running / start_daemon / stop_daemon（客户端管控）
使用 mock 替代共享内存与子进程操作（无 socket、无端口）。
"""

import os
import sys
import time
import pytest
from unittest.mock import patch, MagicMock

from src.protocol.daemon_utils import (
    pid_exists,
    heartbeat_fresh,
    find_daemon_pid,
)
from src.client.controller import (
    is_running,
    start_daemon,
    stop_daemon,
)


class TestPidExists:
    """pid_exists 测试（协议层）"""

    def test_current_pid_exists(self):
        assert pid_exists(os.getpid()) is True

    def test_nonexistent_pid(self):
        assert pid_exists(99999999) is False

    def test_init_pid_exists(self):
        if sys.platform == "win32":
            assert pid_exists(0) is False
        else:
            assert pid_exists(1) is True


class TestHeartbeatFresh:
    """heartbeat_fresh 测试（协议层）"""

    def test_fresh_heartbeat(self):
        assert heartbeat_fresh(time.time()) is True

    def test_old_heartbeat(self):
        assert heartbeat_fresh(time.time() - 60) is False

    def test_exactly_at_threshold(self):
        assert heartbeat_fresh(time.time()) is True


class TestFindDaemonPid:
    """find_daemon_pid 测试（协议层）"""

    def test_returns_none_when_shm_empty(self):
        with patch("src.protocol.daemon_utils.read_daemon_info",
                   return_value=None):
            assert find_daemon_pid() is None

    def test_returns_none_when_not_running(self):
        with patch("src.protocol.daemon_utils.read_daemon_info",
                   return_value=(os.getpid(), False, time.time())), \
             patch("src.protocol.daemon_utils.cleanup_shm_resources"):
            assert find_daemon_pid() is None

    def test_returns_none_when_pid_dead(self):
        with patch("src.protocol.daemon_utils.read_daemon_info",
                   return_value=(99999999, True, time.time())), \
             patch("src.protocol.daemon_utils.cleanup_shm_resources"):
            assert find_daemon_pid() is None

    def test_returns_none_when_heartbeat_stale(self):
        with patch("src.protocol.daemon_utils.read_daemon_info",
                   return_value=(os.getpid(), True, time.time() - 60)), \
             patch("src.protocol.daemon_utils.cleanup_shm_resources"):
            assert find_daemon_pid() is None

    def test_returns_pid_when_healthy(self):
        with patch("src.protocol.daemon_utils.read_daemon_info",
                   return_value=(os.getpid(), True, time.time())):
            assert find_daemon_pid() == os.getpid()

    def test_cleans_up_when_pid_dead(self):
        with patch("src.protocol.daemon_utils.read_daemon_info",
                   return_value=(99999999, True, time.time())):
            with patch("src.protocol.daemon_utils.cleanup_shm_resources") as m:
                assert find_daemon_pid() is None
                m.assert_called_once()


class TestControllerIsRunning:
    """客户端 is_running 测试"""

    def test_not_running_when_no_daemon(self):
        with patch("src.client.controller.daemon_running",
                   return_value=False):
            assert is_running() is False

    def test_running_when_daemon_healthy(self):
        with patch("src.client.controller.daemon_running",
                   return_value=True):
            assert is_running() is True


class TestControllerStart:
    """客户端 start_daemon 测试"""

    def test_skips_when_already_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.controller._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.client.controller.is_running", return_value=True):
            start_daemon()
        assert any("已在运行中" in p for p in printed)

    def test_starts_new_daemon_when_not_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.controller._safe_print",
            lambda s: printed.append(s),
        )
        count = {"n": 0}

        def fake_running():
            count["n"] += 1
            return count["n"] >= 2  # 首次未运行，之后启动成功

        fake_file = MagicMock()
        fake_file.__enter__ = MagicMock(return_value=fake_file)
        fake_file.__exit__ = MagicMock(return_value=False)

        with patch("src.client.controller.is_running",
                   side_effect=fake_running), \
             patch("src.client.controller.subprocess.Popen") as mock_popen, \
             patch("src.client.controller.os.makedirs"), \
             patch("src.client.controller.time.sleep"), \
             patch("builtins.open", return_value=fake_file):
            mock_proc = MagicMock()
            mock_proc.pid = 1234
            mock_popen.return_value = mock_proc
            start_daemon()
            assert mock_popen.called


class TestControllerStop:
    """客户端 stop_daemon 测试"""

    def test_not_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.controller._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.client.controller.find_daemon_pid",
                   return_value=None), \
             patch("src.client.controller.cleanup_shm_resources"):
            stop_daemon()
        assert any("未运行" in p for p in printed)

    def test_stop_via_roundtrip(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.controller._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.client.controller.find_daemon_pid",
                   return_value=os.getpid()), \
             patch("src.client.controller.roundtrip",
                   return_value={"type": "ok"}), \
             patch("src.client.controller.cleanup_shm_resources"):
            stop_daemon()
        assert any("已停止" in p for p in printed)

    def test_force_kill_when_roundtrip_fails_and_pid_dead(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.controller._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.client.controller.find_daemon_pid",
                   return_value=99999999), \
             patch("src.client.controller.roundtrip",
                   return_value={"type": "error"}), \
             patch("src.client.controller._pid_exists",
                   return_value=False), \
             patch("src.client.controller.cleanup_shm_resources"):
            stop_daemon()
        assert not any("已停止" in p for p in printed)

    def test_force_kill_when_roundtrip_fails_and_pid_alive(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.controller._safe_print",
            lambda s: printed.append(s),
        )
        with patch("src.client.controller.find_daemon_pid",
                   return_value=os.getpid()), \
             patch("src.client.controller.roundtrip",
                   return_value={"type": "error"}), \
             patch("src.client.controller._pid_exists",
                   return_value=True), \
             patch("src.client.controller.os.system") as mock_system, \
             patch("src.client.controller.cleanup_shm_resources"):
            stop_daemon()
        mock_system.assert_called_once()
        assert any("已停止" in p for p in printed)