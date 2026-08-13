"""PseudoTerminal 抽象基类

定义了最小接口契约，所有具体 PTY 后端必须实现全部方法。

进程管理职责（kill_tree / 进程列表 / GUI / 通知）已迁出到
`process/` 包（ProcessTreeTracker 抽象），PTY 只负责伪终端 I/O，
进程树追踪通过 `register_root` 委托给 Session 注入的 tracker。
"""

import logging
from typing import Optional

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

    def resize(self, cols: int, rows: int):
        """调整伪终端尺寸

        Args:
            cols: 新的列数。
            rows: 新的行数。
        """
        pass

    def close(self):
        """关闭 PTY 并清理资源"""
        raise NotImplementedError

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

    def inject_mouse_event(self, x: int, y: int, button: int, is_release: bool, control_key_state: int = 0) -> bool:
        """向子进程控制台直接注入鼠标事件（Windows ConPTY 专用）

        ConPTY 输入管道不会把 SGR 鼠标序列转换为子进程的 MOUSE_EVENT_RECORD，
        因此需要通过 Windows Console API 直接注入。默认实现返回 False，
        表示后端不支持或不需此功能。

        Args:
            x: 鼠标列坐标（0-based，相对于终端缓冲区）。
            y: 鼠标行坐标（0-based，相对于终端缓冲区）。
            button: 鼠标按钮状态位（与 SGR 编码一致：0=左键, 1=中键, 2=右键, 3=无）。
            is_release: 是否为释放事件。
            control_key_state: 控制键状态（Windows 控制台格式）。

        Returns:
            True 表示注入成功。
        """
        return False

    # ── GUI 窗口检测（可选能力，默认空实现）──
    # Unix 后端在终端窗口上检测（pty_impl.poll_gui_windows），
    # Windows ConPTY 无自有窗口（headless），保持空实现。

    def poll_gui_windows(self) -> list:
        """轮询检测本轮新增的 GUI 窗口（无窗口后端返回空列表）"""
        return []

    def get_gui_windows(self) -> list:
        """获取已检测到的 GUI 窗口列表"""
        return []

    def close_gui_window(self, hwnd: int) -> bool:
        """关闭指定 GUI 窗口"""
        return False
