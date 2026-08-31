"""win-sandbox 沙箱集成测试（真实 win_sandbox_native + ConPTY）

覆盖：manager 全链路（start/start_process/query/terminate/close）、
SandboxPty 完整终端语义（回显/退出码/进程树终止）。
隔离机制：WRITE_RESTRICTED 受限令牌 + Job（写受 capability SID 限制，
读/网络/进程可见性不受限）。
"""

import glob
import os
import sys
import time

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# win-sandbox 为 Windows 原生组件；非 Windows 平台模块级跳过（import 前）
if sys.platform != "win32":
    pytest.skip("win-sandbox 仅支持 Windows", allow_module_level=True)

from src.sandbox.manager import SandboxSessionManager
from src.sandbox.tracker import SandboxProcessTreeTracker
from src.sandbox.pty import SandboxPty

# vendored 原生库存在性（win_sandbox 包 + pyd）
_PKG_DIR = os.path.join(_PROJECT_ROOT, "bin", "win_sandbox")
_HAS_SANDBOX = os.path.isfile(os.path.join(_PKG_DIR, "__init__.py")) and bool(
    glob.glob(os.path.join(_PKG_DIR, "_native", "win_sandbox_native*.pyd"))
)

# 沙箱隔离：无网络隔离 + 剪贴板不隔离
_ISOLATION = {
    "net_policy": "unrestricted",
    "net_allowlist": [],
    "clipboard_isolate": False,
}
_QUOTA = {"memory_mb": 256, "max_processes": 64, "crash_silent": True}


def _make_manager() -> SandboxSessionManager:
    return SandboxSessionManager(
        quota=dict(_QUOTA),
        isolation=dict(_ISOLATION),
        log_level="info",
    )


def _read_until(pty, needle: bytes, timeout=15.0) -> bytes:
    """阻塞收集输出直到出现 needle（或根进程退出）"""
    out = bytearray()
    t0 = time.time()
    while time.time() - t0 < timeout:
        data = pty.read(65536)
        if not data:
            if pty.get_exit_code() is not None:
                break
            continue
        out.extend(data)
        if needle in out:
            break
    return bytes(out)


@pytest.mark.skipif(not _HAS_SANDBOX, reason="win_sandbox_native 未构建")
class TestManagerE2E:
    """真实沙箱：管理器全链路"""

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        self._managers = []
        yield
        for m in self._managers:
            try:
                m.close()
            except Exception:
                pass

    def _manager(self):
        m = _make_manager()
        self._managers.append(m)
        m.start()
        return m

    def test_start_and_ready(self):
        m = self._manager()
        assert m._instance is not None
        # 原生实例可用：经 manager 启动进程并查询 Job 内进程列表
        m.start_process("cmd.exe /c ping -n 2 127.0.0.1 >nul & echo ready", working_dir=os.getcwd())
        import time
        time.sleep(1.0)
        assert m.get_process_list()
        m.terminate()

    def test_run_echo_and_exit_code(self):
        # SandboxPty 全链路：ConPTY 输出回显 + Job 退出码
        m = self._manager()
        tracker = SandboxProcessTreeTracker(m)
        pty = SandboxPty(["cmd.exe", "/c", "echo", "sandbox-it-ok"],
                         80, 24, cwd=None, tracker=tracker, manager=m)
        out = _read_until(pty, b"sandbox-it-ok")
        assert b"sandbox-it-ok" in out
        t0 = time.time()
        while time.time() - t0 < 10:
            if pty.get_exit_code() is not None:
                break
            time.sleep(0.1)
        assert pty.get_exit_code() == 0
        assert not tracker.is_root_alive()
        pty.close()

    def test_exit_code_nonzero(self):
        m = self._manager()
        tracker = SandboxProcessTreeTracker(m)
        pty = SandboxPty(["cmd.exe", "/c", "exit", "7"],
                         80, 24, tracker=tracker, manager=m)
        t0 = time.time()
        while time.time() - t0 < 10:
            if pty.get_exit_code() is not None:
                break
            time.sleep(0.1)
        assert pty.get_exit_code() == 7
        pty.close()

    def test_write_stdin_roundtrip(self):
        # 交互模式：写 stdin → 进程回显输出（ConPTY 输入管道）
        m = self._manager()
        tracker = SandboxProcessTreeTracker(m)
        pty = SandboxPty(["cmd.exe"], 80, 24, tracker=tracker, manager=m)
        pty.write(b"echo stdin-ok\r\n")
        out = _read_until(pty, b"stdin-ok", timeout=20.0)
        assert b"stdin-ok" in out
        pty.write(b"exit\r\n")
        pty.close()

    def test_process_list_and_exit_code_query(self):
        m = self._manager()
        tracker = SandboxProcessTreeTracker(m)
        pty = SandboxPty(["cmd.exe", "/k", "echo", "query-root"],
                         80, 24, tracker=tracker, manager=m)
        time.sleep(1.0)
        pids = m.get_process_list()
        assert pty.get_child_pid() in pids  # 根进程在 Job 内
        # 进程运行中：退出码查询返回 None
        assert m.get_process_exit_code(pty.get_child_pid()) is None
        m.terminate(exit_code=0)
        t0 = time.time()
        while time.time() - t0 < 10:
            if pty.get_exit_code() is not None:
                break
            time.sleep(0.1)
        assert pty.get_exit_code() == 0
        pty.close()

    def test_terminate_kills_all(self):
        m = self._manager()
        tracker = SandboxProcessTreeTracker(m)
        pty = SandboxPty(["cmd.exe", "/k", "echo", "term-root"],
                         80, 24, tracker=tracker, manager=m)
        time.sleep(1.0)
        assert tracker.is_root_alive()
        m.terminate(exit_code=9)
        t0 = time.time()
        while time.time() - t0 < 10:
            if not tracker.is_root_alive():
                break
            time.sleep(0.1)
        # KILL_ON_JOB：进程树全灭
        assert not tracker.is_root_alive()
        pty.close()

    def test_close_idempotent(self):
        m = self._manager()
        m.close()
        m.close()


@pytest.mark.skipif(not _HAS_SANDBOX, reason="win_sandbox_native 未构建")
class TestTrackerAndPtyE2E:
    """真实沙箱：tracker 端口 + SandboxPty 桥接"""

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        self._trackers = []
        yield
        for t in self._trackers:
            try:
                t.close()
            except Exception:
                pass

    def _tracker(self):
        mgr = _make_manager()
        t = SandboxProcessTreeTracker(mgr)
        self._trackers.append(t)
        return t

    def test_tracker_root_and_list(self):
        t = self._tracker()
        pty = SandboxPty(["cmd.exe", "/c", "echo", "trk-ok"],
                         80, 24, tracker=t, manager=t.manager)
        out = _read_until(pty, b"trk-ok")
        assert b"trk-ok" in out
        t0 = time.time()
        while time.time() - t0 < 10:
            if t.get_root_exit_code() is not None:
                break
            time.sleep(0.1)
        assert t.get_root_exit_code() == 0
        pty.close()

    def test_kill_tree_through_tracker(self):
        t = self._tracker()
        pty = SandboxPty(["cmd.exe", "/k", "echo", "kill-root"],
                         80, 24, tracker=t, manager=t.manager)
        time.sleep(1.0)
        assert t.is_root_alive()
        t.kill_tree()
        t0 = time.time()
        while time.time() - t0 < 10:
            if not t.is_root_alive():
                break
            time.sleep(0.1)
        assert not t.is_root_alive()
        pty.close()