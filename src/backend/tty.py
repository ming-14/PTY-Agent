"""TTY 后端签发 — Windows ConPTY / Unix openpty

按当前平台导出对应的 TTY 后端实现类。Tty 模式需要真实终端终端仿真，
创建失败会直接抛错（不降级到 subprocess）。
"""

import logging

from ..config import IS_WINDOWS

_logger = logging.getLogger("backend-tty")

if IS_WINDOWS:
    from .windows.kernel32_api import WinTtyBackend
    TtyBackend = WinTtyBackend  # noqa: F401
else:
    from .unix.pty import UnixTtyBackend
    TtyBackend = UnixTtyBackend  # noqa: F401


__all__ = ["TtyBackend"]