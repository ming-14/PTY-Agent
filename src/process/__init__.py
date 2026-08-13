"""进程子包 — 进程信息查询、监控、GUI 窗口检测与进程树追踪端口

分层：
- base：实体层（ProcessNotification 统一通知 + ProcessTreeTracker 抽象端口）
- info / monitor / gui：上层编排（进程信息、进程监控、GUI 检测）
- windows/、unix/：平台实现（Job Object / process group 进程树追踪）
- win32_error：Windows 错误码格式化
"""

from .info import (
    _get_process_name,
    _get_process_path,
    _format_exit_code_message,
    _signal_name,
    _format_pty_error,
)
from .base import ProcessNotification, ProcessTreeTracker
from .monitor import ProcessMonitor
from .gui import GuiDetector
