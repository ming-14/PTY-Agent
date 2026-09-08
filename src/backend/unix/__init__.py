"""Unix TTY 实现包

提供 Unix 平台的伪终端后端：

- UnixTtyBackend — 基于 os.openpty + os.fork + os.execvpe
- UnixProcessTracker — /proc 进程树追踪（对齐 Windows Job Object 能力）

与 Windows 实现（src/backend/windows/）结构对称、接口对齐。
"""

from .pty import UnixTtyBackend
from .tracker import UnixProcessTracker

__all__ = ["UnixTtyBackend", "UnixProcessTracker"]
