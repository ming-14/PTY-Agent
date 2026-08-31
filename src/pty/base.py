"""PseudoTerminal 抽象基类与跨平台数据结构

定义统一的接口契约，所有 PTY 后端（Windows / Unix / subprocess）必须
实现全部方法。平台实现放在对等的包结构中：

- src/pty/windows/  — Windows 实现（ConPTY / ConDrv）
- src/pty/unix/     — Unix 实现（os.openpty + fork）

能力对齐原则：
- 接口签名统一：两平台提供相同的方法集合。
- 能力有差异的项在文档中标注：无法实现的平台返回空/None（例如
  Unix 没有 Job Object IOCP，get_job_notifications() 返回空列表）。
"""

import logging
from typing import List, Optional

_logger = logging.getLogger("pty-base")


class ProcessEvent:
    """跨平台进程生命周期事件（统一数据结构）

    Windows 后端通过 Job Object IOCP 推送实时事件；
    Unix 后端目前无实时通知机制（接口预留，返回空列表）。

    消费方（session/process/monitor.py）只依赖本类公开的
    is_spawn() / is_exit() / is_crash() / pid / exit_code，
    不感知底层平台。
    """

    # 平台无关的事件类型常量
    KIND_SPAWN = "spawn"
    KIND_EXIT = "exit"
    KIND_CRASH = "crash"

    def __init__(self, kind: str, pid: int = 0,
                 exit_code: Optional[int] = None):
        """创建进程事件

        Args:
            kind:      事件类型，取 KIND_SPAWN / KIND_EXIT / KIND_CRASH。
            pid:       相关进程 PID。
            exit_code: 进程退出码（退出/崩溃事件）。
        """
        self.kind = kind
        self.pid = pid
        self.exit_code = exit_code

    def is_spawn(self) -> bool:
        """是否为进程创建事件"""
        return self.kind == self.KIND_SPAWN

    def is_exit(self) -> bool:
        """是否为进程正常退出事件"""
        return self.kind == self.KIND_EXIT

    def is_crash(self) -> bool:
        """是否为进程异常退出（崩溃）事件"""
        return self.kind == self.KIND_CRASH

    def __repr__(self) -> str:
        return (f"ProcessEvent(kind={self.kind!r}, pid={self.pid}, "
                f"exit_code={self.exit_code})")


class PseudoTerminal:
    """伪终端抽象基类 — 统一接口契约

    所有具体实现必须实现以下方法：
    - read(n) → bytes
    - write(data)
    - close()
    - fileno()
    - get_child_pid()
    - get_exit_code()
    - get_process_list()
    - get_gui_windows()
    - poll_gui_windows()
    - close_gui_window()

    能力差异（两平台对齐但能力不同）：
    - get_process_list()：Windows 返回 Job 进程树全部 PID；
      Unix 通过进程组/遍历返回进程树全部 PID；subprocess 尽力而为。
    - get_job_notifications()：Windows 返回 IOCP 实时事件；
      Unix / subprocess 返回空列表（无等价机制）。
    - GUI 窗口检测：Windows 通过 EnumWindows；Unix / subprocess 返回空。
    """

    def get_type(self) -> str:
        """返回 PTY 后端类型标识

        Returns:
            字符串标识，如 "win-condrv"、"win-conpty"、"unix-pty"、"subprocess"。
        """
        return "unknown"

    def read(self, n: int = 65536) -> bytes:
        """从 PTY 读取最多 n 字节"""
        raise NotImplementedError

    def drain(self, max_bytes: int = 65536) -> bytes:
        """排空管道缓冲区中所有当前已就绪的数据（非阻塞）

        在 read() 返回数据后调用，把同一批次中剩余的 pipe 数据全部取回。
        这么做能避免程序输出被多次 read 打散成多个小 chunk，
        确保触发检测在完整的输出块上进行。

        Args:
            max_bytes: 单次读取的大小上限。

        Returns:
            排空得到的累积数据，无数据时返回 b""。
        """
        return b""

    def write(self, data):
        """写入数据到 PTY"""
        raise NotImplementedError

    def close(self):
        """关闭 PTY 并清理资源"""
        raise NotImplementedError

    def kill_tree(self):
        """强杀整个进程树（不等待退出），close() 仍需调用以清理资源"""

    def fileno(self):
        """返回 PTY 的文件描述符（如适用）"""
        return None

    def get_child_pid(self):
        """返回子进程 PID（如适用）"""
        return None

    def get_exit_code(self) -> Optional[int]:
        """获取子进程退出码

        返回 None 表示进程仍在运行或无法获取退出码。
        返回 int 表示进程已退出，值为退出码。

        Returns:
            Optional[int]: 退出码或 None。
        """
        return None

    def get_child_process_exit_code(self, pid: int) -> Optional[int]:
        """查询子/孙进程退出码

        用于检测子进程崩溃：即使主进程正常退出，子进程异常退出
        也能被检测到。

        - Windows：通过 Job Object 查询。
        - Unix：仅对直接子进程可用（waitpid），孙进程返回 None。
        - subprocess：Windows 通过 Job Object，其余返回 None。

        Args:
            pid: 子/孙进程 ID。

        Returns:
            退出码（int），若无法查询或进程仍在运行则返回 None。
        """
        return None

    def get_job_notifications(self) -> List[ProcessEvent]:
        """获取实时进程事件通知

        Windows 后端通过 Job Object IOCP 返回 ProcessEvent 列表，
        由 Session 的 ProcessMonitor 消费。
        Unix / subprocess 后端返回空列表（无 IOCP 等价机制）。

        Returns:
            ProcessEvent 列表。
        """
        return []

    # ---- 进程树追踪 ----

    def get_process_list(self) -> List[int]:
        """获取进程树所有进程的 PID 列表

        - Windows 后端通过 Job Object 查询所有子/孙进程 PID。
        - Unix 后端通过 /proc 遍历构建进程树。
        - subprocess 后端：Windows 通过 Job Object，其余仅返回直接子进程。

        Returns:
            PID 列表。
        """
        pid = self.get_child_pid()
        return [pid] if pid is not None else []

    # ---- GUI 窗口检测（Windows 专有能力，其余平台返回空）----

    def get_gui_windows(self) -> List[dict]:
        """获取已检测到的 GUI 窗口列表

        Returns:
            窗口信息字典列表，每项含 hwnd/pid/title/class_name。
            Windows 后端返回实际信息，其他后端返回空列表。
        """
        return []

    def poll_gui_windows(self) -> List[dict]:
        """轮询检测新增 GUI 窗口

        与 get_gui_windows 不同，此方法执行一次新的扫描，
        仅返回本轮新增的窗口。

        Returns:
            本轮新增的窗口信息字典列表。
        """
        return []

    def close_gui_window(self, hwnd: int) -> bool:
        """关闭指定 GUI 窗口

        Args:
            hwnd: 窗口句柄。

        Returns:
            True 表示关闭请求已发送。
        """
        return False
