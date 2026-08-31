"""沙箱委派包 —— 原生 C++ 沙箱集成（Windows 专属）

把 sandbox 原生库（Job Object + 受限令牌 + pybind11）作为
会话的完整后端：
  - SandboxSessionManager：原生沙箱实例会话（进程内直调 + 回调通知流）
  - SandboxPty：PseudoTerminal 端口实现（wezterm-py Pty 创建 ConPTY +
    外部传入 hpcon，回显/方向键/resize/Ctrl+C 与原生 ConPTY 一致）
  - SandboxProcessTreeTracker：ProcessTreeTracker 端口实现（进程树/通知/终止）

启用方式：config/daemon/sandbox.toml 的 [sandbox] enabled = true。
依赖：bin/win_sandbox/_native/win_sandbox_native*.pyd（pybind11 扩展，
由 BUILD.py 编译复制到 bin/win_sandbox/_native/，经 vendored 包 win_sandbox 加载）。
"""

from .manager import SandboxError, SandboxSessionManager
from .pty import SandboxPty
from .tracker import SandboxProcessTreeTracker

__all__ = [
    "SandboxError",
    "SandboxProcessTreeTracker",
    "SandboxPty",
    "SandboxSessionManager",
]
