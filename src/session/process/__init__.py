"""进程子包 — 进程监控、GUI 检测与查询工具"""

from .info import (
    _get_process_name,
    _get_process_path,
    _format_exit_code_message,
    _format_pty_error,
)
from .monitor import ProcessMonitor
from .gui import GuiDetector
