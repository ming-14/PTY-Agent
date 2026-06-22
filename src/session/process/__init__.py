"""进程子包 — 进程信息查询、监控与 GUI 窗口检测"""

from .info import (
    _get_process_name,
    _get_process_path,
    _format_exit_code_message,
    _signal_name,
    _format_pty_error,
)
from .monitor import ProcessMonitor
from .gui import GuiDetector
