"""win_sandbox 异常类型：SandboxError / SandboxTimeoutError / SandboxProcessError / ProtocolError。

层级：
    SandboxError             所有异常基类
    ├── SandboxTimeoutError  超时
    ├── ProtocolError        配置错误（未知字段、非法枚举、路径展开失败）
    └── SandboxProcessError  进程异常退出或启动失败
"""

from __future__ import annotations


class SandboxError(Exception):
    """沙箱相关错误基类。"""


class SandboxTimeoutError(SandboxError):
    """等待进程退出 / IO 完成超时。"""


class ProtocolError(SandboxError):
    """配置错误：未知字段、非法枚举、路径展开失败（严格模式拒绝加载）。"""


class SandboxProcessError(SandboxError):
    """沙箱进程异常退出或启动失败。"""
