"""伪终端后端层 — 工厂函数与平台检测

提供 create_pty() 工厂函数，按优先级尝试各后端实现。

平台包结构（对称、接口对齐）：
  - src/pty/unix/     — Unix 实现（UnixPseudoTerminal + UnixProcessTracker）
  - src/pty/windows/  — Windows 实现（ConPTY + ProcessJob + GuiWindowMonitor）
  - src/pty/base.py   — 统一抽象接口（PseudoTerminal + ProcessEvent）
  - src/pty/errors.py — 跨平台退出码/错误格式化
"""

import logging
from typing import Optional
from ..config import IS_WINDOWS
from .subprocess import SubprocessPseudoTerminal

_logger = logging.getLogger("pty-factory")

if IS_WINDOWS:
    from .windows.kernel32_api import WindowsPseudoTerminal
else:
    from .unix import UnixPseudoTerminal


def create_pty(command, cols: int = 80, rows: int = 24, shell: Optional[str] = None,
               cwd: Optional[str] = None):
    """创建最优可用的 PTY 后端实例

    优先级:
      Windows: kernel32.CreatePseudoConsole > subprocess 管道
      Unix:    UnixPseudoTerminal > subprocess 管道

    Args:
        command: 命令字符串或字符串列表。
        cols:    终端宽度（列数），默认 80。
        rows:    终端高度（行数），默认 24。
        shell:   指定解释器（cmd/powershell/pwsh/bash），默认 powershell（不可用回退 cmd）。
        cwd:     子进程工作目录（默认守护进程当前目录）。

    Returns:
        PseudoTerminal 子类实例。

    Raises:
        RuntimeError: 所有后端均创建失败时抛出。
    """
    if isinstance(command, str):
        _logger.info("create_pty: string command, using Subprocess shell=%s cwd=%s cmd=%r", shell, cwd, command[:200])
        return SubprocessPseudoTerminal(command, cols, rows, shell=shell, cwd=cwd)

    if IS_WINDOWS:
        try:
            _logger.info("create_pty: trying WindowsPseudoTerminal (ConPTY)")
            return WindowsPseudoTerminal(command, cols, rows, cwd=cwd)
        except Exception as e:
            _logger.warning("create_pty: WindowsPseudoTerminal failed: %s, falling back to Subprocess", e)
        _logger.info("create_pty: fallback to SubprocessPseudoTerminal")
        return SubprocessPseudoTerminal(command, cols, rows, cwd=cwd)

    try:
        _logger.info("create_pty: trying UnixPseudoTerminal")
        return UnixPseudoTerminal(command, cols, rows, cwd=cwd)
    except Exception as e:
        _logger.warning("create_pty: UnixPseudoTerminal failed: %s, falling back to Subprocess", e)
        return SubprocessPseudoTerminal(command, cols, rows, cwd=cwd)
