"""Unix PTY 实现包

提供 Unix 平台的伪终端后端：

- UnixPseudoTerminal — 基于 os.openpty + os.fork + os.execvpe
- UnixProcessTracker — /proc 进程树追踪（对齐 Windows Job Object 能力）

与 Windows 实现（src/pty/windows/）结构对称、接口对齐。
"""

from .pty import UnixPseudoTerminal
from .tracker import UnixProcessTracker

__all__ = ["UnixPseudoTerminal", "UnixProcessTracker"]
