"""沙箱进程树追踪器 —— SandboxProcessTreeTracker（ProcessTreeTracker 端口实现）

win_sandbox_native 委派实现：进程由 C++ 核心启动（天然在 WRITE_RESTRICTED
受限令牌 + Job 隔离内），本实现把 ProcessTreeTracker 端口全部委托给
SandboxSessionManager 的原生能力：

| 端口方法                | win_sandbox_native 能力                 |
|-------------------------|-----------------------------------------|
| register_root           | SandboxPty spawn 后登记 OS pid          |
| get_process_list        | Process.query_process_list              |
| get_process_exit_code   | Process.query_process_exit_code         |
| get_root_exit_code      | Job 退出回调（根 pid 匹配）             |
| kill_tree               | Process.terminate（KILL_ON_JOB 全灭）   |
| drain_notifications     | on_job_process_started/exited 回调队列  |
| GUI 三件套              | 空实现（沙箱隔离下本地 EnumWindows 不适用）|
| close                   | SandboxInstance.shutdown（C++ 清理 grants/temp）|

生命周期归属 Session（与 JobProcessTreeTracker 一致）：kill_tree → pty.close → tracker.close。
"""

from typing import List, Optional

from ..process.base import ProcessNotification, ProcessTreeTracker
from .manager import SandboxSessionManager
from ..logging import get_logger

_logger = get_logger("sandbox-tracker")


class SandboxProcessTreeTracker(ProcessTreeTracker):
    """win_sandbox_native 沙箱进程树追踪器（Windows 专属）"""

    def __init__(self, manager: SandboxSessionManager):
        self._manager = manager
        self._root_pid: Optional[int] = None

    @property
    def manager(self) -> SandboxSessionManager:
        """沙箱会话管理器（SandboxPty 工厂经此获取共享实例）"""
        return self._manager

    # ── 登记 ──

    def register_root(self, pid: int, hprocess: Optional[int] = None) -> bool:
        """登记根进程 OS pid（沙箱内进程由原生实例启动，无需 assign）

        Args:
            pid:      主进程 OS PID（start_process 返回）。
            hprocess: 沙箱场景无本地句柄，忽略。
        """
        self._root_pid = pid
        _logger.debug("sandbox register_root: os_pid=%s", pid)
        return True

    # ── 进程树查询 ──

    def get_process_list(self) -> List[int]:
        """查询沙箱 Job 内全部 PID 列表"""
        return self._manager.get_process_list()

    def is_root_alive(self) -> bool:
        return self._manager.is_root_alive()

    # ── 终止 ──

    def kill_tree(self, timeout: float = 3.0) -> None:
        """终止沙箱 Job 内全部进程（KILL_ON_JOB 语义，timeout 由沙箱端保证）"""
        self._manager.terminate()

    # ── 退出码 ──

    def get_root_exit_code(self) -> Optional[int]:
        return self._manager.get_exit_code()

    def get_process_exit_code(self, pid: int) -> Optional[int]:
        return self._manager.get_process_exit_code(pid)

    # ── 通知 ──

    def drain_notifications(self) -> List[ProcessNotification]:
        return self._manager.drain_notifications()

    # ── GUI 窗口（沙箱场景不适用，保持抽象默认空实现）──

    # ── 生命周期（归 Session）──

    def close(self):
        """关闭沙箱（原生 shutdown，终止所有进程并清理）"""
        self._manager.close()
