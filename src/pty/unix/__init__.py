"""Unix PTY 子包 — UnixPseudoTerminal 与 Shell 检测

与 windows/ 子包对称：
- pty_impl.UnixPseudoTerminal  ←→  windows.conpty.WindowsPseudoTerminal
- shells                       ←→  windows.shells

进程管理（process group 追踪）已迁出到 `process/` 包
（ProcessTreeTracker 抽象，见 design/process-manager-refactor.md）。
"""

from .pty_impl import UnixPseudoTerminal
from .shells import detect_available_shells, format_shell_info

__all__ = [
    "UnixPseudoTerminal",
    "detect_available_shells", "format_shell_info",
]
