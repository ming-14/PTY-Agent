"""backend 工厂 — 两种后端模式的显式创建入口

subprocess 与 tty 彻底分离，无回退链：

- create_subprocess() — 纯管道子进程（字符串命令可经 shell 执行）
- create_tty()        — 真实终端（命令必须是列表，无 shell 语法）；
                        创建失败直接抛 RuntimeError，不降级到 subprocess
"""

import logging
from typing import Optional

from ..config import IS_WINDOWS
from .subprocess import SubprocessBackend
from .tty import TtyBackend

_logger = logging.getLogger("backend-factory")


def create_subprocess(command, shell: Optional[str] = None,
                      cwd: Optional[str] = None,
                      cols: int = 80, rows: int = 24) -> SubprocessBackend:
    """创建 subprocess 管道后端（默认模式）

    Args:
        command: 命令字符串（经 shell 执行，支持 | && > 等语法）。
        shell:   指定解释器（cmd/powershell/pwsh/bash），默认 powershell
                 （不可用时回退 cmd）。
        cwd:     子进程工作目录（默认继承调用者）。
        cols:    终端宽度（仅语义占位，管道模式无真实终端）。
        rows:    终端高度（仅语义占位）。

    Returns:
        SubprocessBackend 实例。

    Raises:
        RuntimeError: 子进程启动失败。
    """
    _logger.info("create_subprocess: shell=%s cwd=%s cmd=%r",
                 shell, cwd, command[:200] if isinstance(command, str) else command)
    return SubprocessBackend(command, cols, rows, shell=shell, cwd=cwd)


def create_tty(command, cols: int = 80, rows: int = 24,
               cwd: Optional[str] = None) -> TtyBackend:
    """创建真实终端（TTY）后端（--pty 模式）

    Args:
        command: 命令列表（已由调用方 shlex 拆分，不支持 shell 语法）。
        cols:    终端宽度（列数），默认 80。
        rows:    终端高度（行数），默认 24。
        cwd:     子进程工作目录（默认继承调用者）。

    Returns:
        TtyBackend 实例。

    Raises:
        RuntimeError: TTY 创建失败（不降级到 subprocess）。
    """
    _logger.info("create_tty: platform=%s cmd=%r cols=%s rows=%s cwd=%s",
                 "windows" if IS_WINDOWS else "unix", command, cols, rows, cwd)
    return TtyBackend(command, cols, rows, cwd=cwd)