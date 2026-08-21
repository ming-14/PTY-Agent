"""进程管理层抽象端口 — ProcessNotification 统一通知实体 + ProcessTreeTracker 抽象基类

进程树追踪、批量终止、实时通知、退出码查询统一抽象为 ProcessTreeTracker 端口，
由各平台实现（Windows Job Object / Unix process group / 未来 winsandbox 委派），
上层（ProcessMonitor / GuiDetector / Session）只依赖本抽象，不感知具体实现。

生命周期约定：
- tracker 由 Session 创建并持有（owner）
- PTY spawn 子进程后通过 register_root() 登记根进程（紧贴 spawn 调用，防止逃逸）
- 终止顺序由 Session 显式控制：kill_tree() → pty.close() → tracker.close()
"""

from ..logging import get_logger
from abc import ABC, abstractmethod
from typing import List, Optional

_logger = get_logger("process-base")

# 通知类型（跨平台统一字符串）
NOTIF_SPAWN = "spawn"
NOTIF_EXIT = "exit"
NOTIF_CRASH = "crash"


class ProcessNotification:
    """统一进程事件通知（合并 Windows JobNotification 与 UnixNotification）

    Attributes:
        type:          事件类型，取值 NOTIF_SPAWN / NOTIF_EXIT / NOTIF_CRASH。
        pid:           相关进程 PID。
        exit_code:     退出码（exit/crash 事件）；spawn 事件为 None。
        process_name:  进程名（Windows IOCP NEW_PROCESS 时尽力填充；Unix 为空）。
        process_path:  进程完整路径（同上）。
    """

    def __init__(
        self,
        type: str,
        pid: int,
        exit_code: Optional[int] = None,
        process_name: str = "",
        process_path: str = "",
    ):
        self.type = type
        self.pid = pid
        self.exit_code = exit_code
        self.process_name = process_name
        self.process_path = process_path

    def is_spawn(self) -> bool:
        return self.type == NOTIF_SPAWN

    def is_exit(self) -> bool:
        return self.type == NOTIF_EXIT

    def is_crash(self) -> bool:
        return self.type == NOTIF_CRASH

    def __repr__(self):
        return (
            f"ProcessNotification({self.type}, pid={self.pid}, "
            f"exit_code={self.exit_code})"
        )


class ProcessTreeTracker(ABC):
    """进程树追踪器抽象端口

    提供进程树追踪、批量终止、实时通知、退出码查询能力。
    GUI 窗口检测为可选能力（默认空实现），仅 Windows 实现有效。

    实现要求：
    - register_root() 在 PTY spawn 成功后立即调用，实现内部必须同步完成
      根进程入组（Windows: AssignProcessToJobObject；Unix: getpgid 捕获），
      否则子进程 spawn 的孙进程可能逃逸出追踪范围。
    """

    # ── 登记 ──

    @abstractmethod
    def register_root(self, pid: int, hprocess: Optional[int] = None) -> bool:
        """登记根进程（PTY spawn 后立即调用）

        Args:
            pid:       根进程 PID。
            hprocess:  Windows 平台进程句柄（OpenProcess/CreateProcess 返回）；
                       其他平台传 None。

        Returns:
            True 登记成功，False 失败（如句柄无效、进程已属其他 Job）。
        """

    # ── 进程树查询 ──

    @abstractmethod
    def get_process_list(self) -> List[int]:
        """获取进程树内所有进程的 PID 列表"""

    def get_work_process_list(self) -> List[int]:
        """获取进程树内的工作进程 PID 列表（排除宿主进程）

        默认与 get_process_list 一致；Windows Job 实现把 ConPTY 宿主
        （OpenConsole）并入 Job 后需覆写排除宿主，供会话自然结束检测
        （宿主在 PTY 关闭前常驻，若不排除将永远检测不到工作进程全退）。
        """
        return self.get_process_list()

    def register_host_pid(self, pid: int):
        """登记宿主进程（如 OpenConsole），默认空实现（无宿主概念的平台忽略）。"""

    def is_host_process(self, pid: int) -> bool:
        """pid 是否为已登记的宿主进程（如 ConPTY 宿主 OpenConsole）

        宿主进程不视为工作进程，其退出/崩溃不影响程序结果判定，
        上层（ProcessMonitor）据此过滤事件；无宿主概念的平台恒 False。
        """
        return False

    def get_process_count(self) -> int:
        """获取进程树内当前进程数"""
        return len(self.get_process_list())

    @abstractmethod
    def is_root_alive(self) -> bool:
        """检测根进程是否存活"""

    # ── 终止 ──

    @abstractmethod
    def kill_tree(self, timeout: float = 3.0):
        """终止整个进程树（先优雅后强杀）

        Args:
            timeout: 优雅终止后的等待超时秒数，超时后强杀兜底。
        """

    # ── 退出码 ──

    @abstractmethod
    def get_root_exit_code(self) -> Optional[int]:
        """获取根进程退出码（None 表示仍在运行或不可查询）

        Unix 实现是唯一 waitpid 收尸直接子进程的点，保证不与其他模块竞争。
        """

    @abstractmethod
    def get_process_exit_code(self, pid: int) -> Optional[int]:
        """查询树内任意 PID 的退出码（None 表示仍在运行或不可查询）"""

    # ── 通知 ──

    @abstractmethod
    def drain_notifications(self) -> List[ProcessNotification]:
        """取出所有待处理的通知（线程安全，取出即清空）"""

    # ── GUI 窗口检测（可选能力，默认空实现）──

    def get_gui_windows(self) -> List[dict]:
        """获取已检测到的 GUI 窗口列表"""
        return []

    def poll_gui_windows(self, pids: Optional[List[int]] = None) -> List[dict]:
        """轮询检测本轮新增的 GUI 窗口

        Args:
            pids: 调用方已获取的进程树 PID 列表（同一 tick 复用，
                  避免与 get_process_list 重复扫描）；None 时自行查询。
        """
        return []

    def close_gui_window(self, hwnd: int) -> bool:
        """关闭指定 GUI 窗口"""
        return False

    # ── 生命周期（归 Session）──

    @abstractmethod
    def close(self):
        """释放底层资源（关闭 Job 句柄 / 停止通知线程等）"""
