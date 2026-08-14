"""伪终端后端子包 — 工厂函数、平台 PTY 实现

公共接口：
- create_pty()              工厂函数，创建最优可用的 PTY 后端
- PseudoTerminal            抽象基类

Shell 探测见 common/shells.py（跨侧共享）。
"""

from .base import PseudoTerminal
from .pty_factory import create_pty

__all__ = [
    "PseudoTerminal",
    "create_pty",
]
