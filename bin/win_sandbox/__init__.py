"""win_sandbox - Windows 进程沙箱隔离（pybind11 in-process 库形态）。

本包直接加载 win_sandbox_native.pyd 扩展，
以 pybind11 in-process 形态交互，无命名管道通信。

用法：
    import win_sandbox
    sb = win_sandbox.SandboxInstance()
    proc = sb.start_process(command_line="cmd.exe /c echo hello")
    ...
    proc.wait()
    sb.shutdown()
"""

from __future__ import annotations

import os as _os
import sys as _sys

# 先导入异常类型（SandboxInstance 构造失败抛 ProtocolError 时需已存在）
from .exceptions import (  # noqa: E402
    SandboxError,
    SandboxProcessError,
    SandboxTimeoutError,
    ProtocolError,
)

# 加载 pybind11 扩展：优先包内 _native/（wheel 安装），回退 build/bin/（开发态）
_native_dir = _os.path.join(_os.path.dirname(__file__), "_native")
if _os.path.isdir(_native_dir):
    _sys.path.insert(0, _native_dir)
else:
    _build_bin = _os.path.join(_os.path.dirname(__file__), "..", "..", "build", "bin")
    _build_bin = _os.path.abspath(_build_bin)
    if _os.path.isdir(_build_bin):
        _sys.path.insert(0, _build_bin)

from win_sandbox_native import SandboxInstance, Process  # noqa: E402
from win_sandbox_native import contains_access_denied_keyword  # noqa: E402
from .helpers import (  # noqa: E402
    read_pipe,
    write_pipe,
    wait_process,
    close_handle,
    WallClockTimer,
    StatsPoller,
    drain_stdout,
    drain_stderr,
)

__version__ = "0.2.0"

__all__ = [
    "SandboxInstance",
    "Process",
    "contains_access_denied_keyword",
    "SandboxError",
    "SandboxTimeoutError",
    "SandboxProcessError",
    "ProtocolError",
    "read_pipe",
    "write_pipe",
    "wait_process",
    "close_handle",
    "WallClockTimer",
    "StatsPoller",
    "drain_stdout",
    "drain_stderr",
    "__version__",
]
