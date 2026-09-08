"""Windows TTY 实现包

提供 Windows 平台的伪终端后端：

- WinTtyBackend — ConPTY（kernel32.CreatePseudoConsole API）
- ProcessJob          — Job Object 进程树追踪 + IOCP 实时通知
- GuiWindowMonitor    — GUI 窗口检测（EnumWindows + Job PID 匹配）

与 Unix 实现（src/backend/unix/）结构对称、接口对齐。
"""

from .kernel32_api import WinTtyBackend
from .job import ProcessJob, JobNotification
from .gui_monitor import GuiWindowMonitor

__all__ = [
    "WinTtyBackend",
    "ProcessJob",
    "JobNotification",
    "GuiWindowMonitor",
]
