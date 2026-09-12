"""守护进程生命周期与管控单元测试

覆盖：
- protocol.daemon_utils：pid_exists / heartbeat_fresh / find_daemon_pid /
  cleanup_shm_resources（守护进程存活检测，client 与 daemon 共用）
- client.controller：is_running / start_daemon / stop_daemon（客户端管控）
- 日志纳管：logger 名单不漂移、daemon 轮转、client 超量截断
使用 mock 替代共享内存与子进程操作（无 socket、无端口）。
"""

import logging
import os
import re
import sys
import time
from pathlib import Path
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


class _FakeOs:
    """只给 daemon_utils 用的假 os 模块

    存在理由：Windows 上 CPython 的 `os.kill` 不是 POSIX 语义，而是
    TerminateProcess —— **连 signal 0 都会真的把目标进程干掉**。POSIX 分支
    里的 `os.kill(pid, 0)` 探活若在 Windows 上真跑，就是自杀（会把跑测试的
    进程连同它的宿主一起带走）。所以这里给被测模块注入桩 os，
    绝不触碰真实 os.kill。
    """

    def __init__(self, raiser=None):
        self.calls = []
        self._raiser = raiser

    def kill(self, pid, sig):
        self.calls.append((pid, sig))
        if self._raiser is not None:
            raise self._raiser


class TestPidExists:
    """pid_exists 测试（协议层）"""

    def test_current_pid_exists(self):
        assert pid_exists(os.getpid()) is True

    def test_nonexistent_pid(self):
        assert pid_exists(99999999) is False

    def test_unix_branch_calls_os_kill(self, monkeypatch):
        """POSIX 分支用 os.kill(pid, 0) 探活（该分支曾因缺 import os 而 NameError）

        给 daemon_utils 注入桩 os：真实 os.kill 在 Windows 上=TerminateProcess，
        真调用等于自杀。这里只验证"以 signal 0 探测"这一语义。
        """
        from src.protocol import daemon_utils

        fake = _FakeOs()
        monkeypatch.setattr(daemon_utils, "IS_WINDOWS", False)
        monkeypatch.setattr(daemon_utils, "os", fake)
        assert daemon_utils.pid_exists(4321) is True
        assert fake.calls == [(4321, 0)]

    def test_unix_branch_dead_pid(self, monkeypatch):
        """POSIX 分支：ProcessLookupError → 判定不存在"""
        from src.protocol import daemon_utils

        fake = _FakeOs(raiser=ProcessLookupError())
        monkeypatch.setattr(daemon_utils, "IS_WINDOWS", False)
        monkeypatch.setattr(daemon_utils, "os", fake)
        assert daemon_utils.pid_exists(4321) is False
        assert fake.calls == [(4321, 0)]

    def test_unix_branch_permission_means_alive(self, monkeypatch):
        """POSIX 分支：PermissionError → 进程存在但无权限，仍判存活"""
        from src.protocol import daemon_utils

        fake = _FakeOs(raiser=PermissionError())
        monkeypatch.setattr(daemon_utils, "IS_WINDOWS", False)
        monkeypatch.setattr(daemon_utils, "os", fake)
        assert daemon_utils.pid_exists(4321) is True

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
            "src.client.controller.safe_print",
            lambda *a, **k: printed.append(a[0]),
        )
        with patch("src.client.controller.is_running", return_value=True):
            start_daemon()
        assert any("已在运行中" in p for p in printed)

    def test_starts_new_daemon_when_not_running(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.controller.safe_print",
            lambda *a, **k: printed.append(a[0]),
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
            "src.client.controller.safe_print",
            lambda *a, **k: printed.append(a[0]),
        )
        with patch("src.client.controller.find_daemon_pid",
                   return_value=None), \
             patch("src.client.controller.cleanup_shm_resources"):
            stop_daemon()
        assert any("未运行" in p for p in printed)

    def test_stop_via_roundtrip(self, monkeypatch):
        printed = []
        monkeypatch.setattr(
            "src.client.controller.safe_print",
            lambda *a, **k: printed.append(a[0]),
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
            "src.client.controller.safe_print",
            lambda *a, **k: printed.append(a[0]),
        )
        with patch("src.client.controller.find_daemon_pid",
                   return_value=99999999), \
             patch("src.client.controller.roundtrip",
                   return_value={"type": "error"}), \
             patch("src.client.controller.pid_exists",
                   return_value=False), \
             patch("src.client.controller.cleanup_shm_resources"):
            stop_daemon()
        assert not any("已停止" in p for p in printed)

    def test_force_kill_when_roundtrip_fails_and_pid_alive(self, monkeypatch):
        """stop 请求失败且进程仍存活 → 强制终止该 PID 并报告已停止

        强杀机制按平台不同（Windows: taskkill 子进程；Unix: SIGKILL），
        断言按平台选择被 patch 的目标，避免只在单一平台成立的断言。
        """
        printed = []
        monkeypatch.setattr(
            "src.client.controller.safe_print",
            lambda *a, **k: printed.append(a[0]),
        )
        kill_target = ("src.client.controller.subprocess.run"
                       if sys.platform == "win32"
                       else "src.client.controller.os.kill")
        with patch("src.client.controller.find_daemon_pid",
                   return_value=os.getpid()), \
             patch("src.client.controller.roundtrip",
                   return_value={"type": "error"}), \
             patch("src.client.controller.pid_exists",
                   return_value=True), \
             patch(kill_target) as mock_kill, \
             patch("src.client.controller.cleanup_shm_resources"):
            stop_daemon()
        mock_kill.assert_called_once()
        assert any("已强制终止" in p for p in printed)
        assert any("已停止" in p for p in printed)


class TestLoggerCoverage:
    """日志纳管测试：logger 名单不漂移 + 两个日志文件的体积控制"""

    def _source_logger_names(self):
        """扫描 src/ 下所有字面量 logging.getLogger("...") 的名字"""
        import src as pkg

        # src 是命名空间包（无 __init__.py），__file__ 为 None
        root = Path(getattr(pkg, "__file__", None) or pkg.__path__[0])
        names = set()
        for py in root.rglob("*.py"):
            names.update(re.findall(
                r"""getLogger\(\s*['"]([^'"]+)['"]""",
                py.read_text(encoding="utf-8"),
            ))
        return names

    def test_every_source_logger_is_managed(self):
        """src 中用到的每个 logger 名都在 config.MANAGED_LOGGERS 里

        漏配的名字不会被 handler 接管：守护进程里等于日志丢失（stderr 被丢弃），
        客户端里等于 WARNING 直接喷进 CLI 输出。
        """
        from src.config import MANAGED_LOGGERS

        missing = self._source_logger_names() - set(MANAGED_LOGGERS)
        assert not missing, f"未登记到 MANAGED_LOGGERS 的 logger: {sorted(missing)}"

    def test_daemon_log_uses_rotating_handler(self, tmp_path, monkeypatch):
        """守护进程日志用 RotatingFileHandler 且带上体积/份数配置"""
        from src.daemon import lifecycle
        from src.config import LOG_MAX_BYTES, LOG_BACKUP_COUNT
        from logging.handlers import RotatingFileHandler

        monkeypatch.setattr(lifecycle, "LOG_DIR", str(tmp_path))
        lifecycle._setup_logging()
        try:
            handlers = [
                h for name in lifecycle.MANAGED_LOGGERS
                for h in logging.getLogger(name).handlers
            ]
            assert handlers, "守护进程未配置任何日志 handler"
            rotating = [h for h in handlers
                        if isinstance(h, RotatingFileHandler)]
            assert rotating, "daemon.log 应使用 RotatingFileHandler"
            assert rotating[0].maxBytes == LOG_MAX_BYTES
            assert rotating[0].backupCount == LOG_BACKUP_COUNT
        finally:
            for h in handlers:
                h.close()
            for name in lifecycle.MANAGED_LOGGERS:
                logging.getLogger(name).handlers.clear()

    def test_client_log_truncated_when_oversized(self, tmp_path, monkeypatch):
        """client.log 超限时以 'w' 重开（截断），未超限时以 'a' 续写"""
        from src.client import controller
        from src.config import LOG_MAX_BYTES

        monkeypatch.setattr(controller, "LOG_DIR", str(tmp_path))
        modes = []
        real_factory = logging.FileHandler

        def spy(path, **kwargs):
            modes.append(kwargs.get("mode"))
            return real_factory(path, **kwargs)

        monkeypatch.setattr(controller.logging, "FileHandler", spy)

        log_file = tmp_path / "client.log"
        controller.setup_client_logging()          # 首次：不存在 → 续写
        assert modes[-1] == "a"

        for name in controller.MANAGED_LOGGERS:
            logging.getLogger(name).handlers.clear()

        log_file.write_text("x" * (LOG_MAX_BYTES + 10), encoding="utf-8")
        controller.setup_client_logging()          # 超限 → 重开截断
        assert modes[-1] == "w"

        for name in controller.MANAGED_LOGGERS:
            logging.getLogger(name).handlers.clear()
