"""win_sandbox 异常类型。

Phase 12：删除 IPC 形态后，移除 ProtocolError7（IPC 协议错误）。
保留 SandboxError / SandboxTimeoutError / SandboxProcessError（pybind11 形态仍需）。

层级：
    SandboxError             所有异常基类
    ├── SandboxTimeoutError  超时
    └── SandboxProcessError  进程异常退出或启动失败
"""

from __future__ import annotations


class SandboxError(Exception):
    """沙箱相关错误基类。"""


class SandboxTimeoutError(SandboxError):
    """等待进程退出 / IO 完成超时。"""


class SandboxProcessError(SandboxError):
    """沙箱进程异常退出或启动失败。"""
