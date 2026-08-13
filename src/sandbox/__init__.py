"""沙箱委派包 —— win-sandbox 集成（Windows 专属）

把 win-sandbox（Job Object + Low IL token + pybind11 原生库）作为
PTY-Agent 会话的完整后端：
  - SandboxSessionManager：原生沙箱实例会话（进程内直调 + 回调通知流）
  - SandboxPty：PseudoTerminal 端口实现（ConPtyHandle + 外部传入 hpcon，
    回显/方向键/resize/Ctrl+C 与原生 ConPTY 一致）
  - SandboxProcessTreeTracker：ProcessTreeTracker 端口实现（进程树/通知/终止）

启用方式：src/config/sandbox.toml 的 [sandbox] enabled = true。
依赖：bin/win_sandbox（vendored python 包 + win_sandbox_native pyd）。
"""

from .manager import SandboxError, SandboxSessionManager
from .pty import SandboxPty
from .tracker import SandboxProcessTreeTracker

__all__ = [
    "SandboxSessionManager",
    "SandboxPty",
    "SandboxProcessTreeTracker",
    "SandboxError",
]