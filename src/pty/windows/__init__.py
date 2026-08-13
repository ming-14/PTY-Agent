"""Windows PTY 子包 — ConPTY、ConDrv 与 Shell 检测

进程管理（Job Object / GUI 检测）已迁出到 `process/` 包
（ProcessTreeTracker 抽象，见 design/process-manager-refactor.md）。
"""

from .conpty import WindowsPseudoTerminal
from .condrv import ConDrvPseudoTerminal
from ...process.win32_error import translate_windows_error, format_process_exit_code, format_create_process_error
from .shells import detect_available_shells, format_shell_info

__all__ = [
    "WindowsPseudoTerminal",
    "ConDrvPseudoTerminal",
    "translate_windows_error", "format_process_exit_code", "format_create_process_error",
    "detect_available_shells", "format_shell_info",
]
