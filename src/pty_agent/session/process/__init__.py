"""进程子包 — 进程监控、GUI 检测与查询工具

对外只暴露以下名字。退出码/信号的格式化归属 backend.errors（跨平台单一
实现），这里直接从它转出，不在 info.py 里留中转别名。
"""

from ...backend.errors import (
    format_exit_code_message as _format_exit_code_message,
)
from .info import (
    _get_process_name,
    _get_process_path,
    _format_backend_error,
)
from .monitor import ProcessMonitor
from .gui import GuiDetector

__all__ = [
    "_get_process_name",
    "_get_process_path",
    "_format_backend_error",
    "_format_exit_code_message",
    "ProcessMonitor",
    "GuiDetector",
]
