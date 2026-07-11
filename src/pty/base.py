"""PseudoTerminal 抽象基类

定义了最小接口契约，所有具体 PTY 后端必须实现全部方法。
"""

import logging
from typing import Optional, List

_logger = logging.getLogger("pty-factory")


class PseudoTerminal:
    """伪终端抽象基类

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
        """查询子/孙进程退出码（通过 Job Object）

        用于检测子进程崩溃：即使主进程正常退出，子进程异常退出
        也能被检测到。非 Windows 后端返回 None。

        Args:
            pid: 子/孙进程 ID。

        Returns:
            退出码（int），若无法查询或进程仍在运行则返回 None。
        """
        return None

    def get_job_notifications(self) -> list:
        """获取 Job Object IOCP 实时通知

        返回 JobNotification 列表，由 Session 的 _drain_job_notifications 消费。
        非 Windows 后端返回空列表。

        Returns:
            JobNotification 列表。
        """
        return []

    # ---- Job Object 进程树追踪 ----

    def get_process_list(self) -> List[int]:
        """获取进程树所有进程的 PID 列表

        Windows 后端通过 Job Object 查询所有子/孙进程 PID。
        Unix 和 subprocess 后端仅返回直接子进程 PID。

        Returns:
            PID 列表。
        """
        pid = self.get_child_pid()
        return [pid] if pid is not None else []

    # ---- GUI 窗口检测 ----

    def get_gui_windows(self) -> List[dict]:
        """获取已检测到的 GUI 窗口列表

        Returns:
            窗口信息字典列表，每项含 hwnd/pid/title/class_name。
            Windows 后端返回实际信息，其他后端返回空列表。
        """
        return []

    def poll_gui_windows(self) -> List[dict]:
        """轮询检测新增 GUI 窗口

        与 get_gui_windows 不同，此方法执行一次新的 EnumWindows 扫描，
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
            True 表示 WM_CLOSE 已发送。
        """
        return False
