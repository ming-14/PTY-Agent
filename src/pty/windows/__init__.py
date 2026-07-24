"""Windows PTY 子包 — ConPTY、ConDrv、Job Object、GUI 窗口检测与 Shell 检测"""

from .conpty import WindowsPseudoTerminal
from .job import ProcessJob, JobNotification
from .gui_monitor import GuiWindowMonitor, GuiWindowInfo
from .win32_error_msg import translate_windows_error, format_process_exit_code, format_create_process_error
from .shells import detect_available_shells, format_shell_info

__all__ = [
    "WindowsPseudoTerminal",
    "ProcessJob", "JobNotification",
    "GuiWindowMonitor", "GuiWindowInfo",
    "translate_windows_error", "format_process_exit_code", "format_create_process_error",
    "detect_available_shells", "format_shell_info",
]
