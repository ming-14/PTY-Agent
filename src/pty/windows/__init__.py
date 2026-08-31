"""Windows PTY 实现包

提供 Windows 平台的伪终端后端：

- WindowsPseudoTerminal — ConPTY（kernel32.CreatePseudoConsole API）
- ConDrvPseudoTerminal — ConDrv 直连（当前禁用，_CONDRV_OK = False）
- ProcessJob          — Job Object 进程树追踪 + IOCP 实时通知
- GuiWindowMonitor    — GUI 窗口检测（EnumWindows + Job PID 匹配）

与 Unix 实现（src/pty/unix/）结构对称、接口对齐。
"""

from .kernel32_api import WindowsPseudoTerminal
from .condrv import ConDrvPseudoTerminal
from .job import ProcessJob, JobNotification
from .gui_monitor import GuiWindowMonitor

__all__ = [
    "WindowsPseudoTerminal",
    "ConDrvPseudoTerminal",
    "ProcessJob",
    "JobNotification",
    "GuiWindowMonitor",
]
