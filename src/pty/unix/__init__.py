"""Unix PTY 子包 — UnixPseudoTerminal、进程监控与 Shell 检测

与 windows/ 子包对称：
- pty_impl.UnixPseudoTerminal  ←→  windows.conpty.WindowsPseudoTerminal
- process.UnixProcessMonitor   ←→  windows.job.ProcessJob + windows.gui_monitor.GuiWindowMonitor
- shells                       ←→  windows.shells
"""

from .pty_impl import UnixPseudoTerminal
from .process import UnixProcessMonitor, UnixNotification
from .shells import detect_available_shells, format_shell_info

__all__ = [
    "UnixPseudoTerminal",
    "UnixProcessMonitor", "UnixNotification",
    "detect_available_shells", "format_shell_info",
]
