"""伪终端后端子包 — 工厂函数、平台 PTY 实现与 Shell 检测

公共接口：
- create_pty()              工厂函数，创建最优可用的 PTY 后端
- PseudoTerminal            抽象基类
- detect_available_shells() 检测系统可用 shell（平台自适应）
- format_shell_info()       格式化 shell 信息字符串
"""

from .pty_factory import create_pty
from .base import PseudoTerminal
from ..config.common import IS_WINDOWS

if IS_WINDOWS:
    from .windows.shells import detect_available_shells, format_shell_info
else:
    from .unix.shells import detect_available_shells, format_shell_info

__all__ = [
    "create_pty", "PseudoTerminal",
    "detect_available_shells", "format_shell_info",
]
