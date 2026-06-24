"""伪终端后端层 — 工厂函数与平台检测

提供 create_pty() 工厂函数，按优先级尝试各后端实现。
Windows 特有代码存放在 windows/ 子包下，Unix 平台零加载。
"""

import logging
from typing import Optional
from ..config import IS_WINDOWS
from .base import PseudoTerminal
from .unix import UnixPseudoTerminal
from .subprocess import SubprocessPseudoTerminal

_logger = logging.getLogger("pty-factory")

if IS_WINDOWS:
    from .windows.kernel32_api import WindowsPseudoTerminal
    from .windows.condrv import ConDrvPseudoTerminal
    from .windows.convars import _CONDRV_OK


def create_pty(command, cols: int = 80, rows: int = 24, shell: Optional[str] = None):
    """创建最优可用的 PTY 后端实例

    优先级:
      Windows: kernel32.CreatePseudoConsole > subprocess 管道
               （ConDrv 直连不可行：conhost VT I/O 走 ConDrv IPC 而非 hStdOutput）
      Unix:    UnixPseudoTerminal > subprocess 管道

    Args:
        command: 命令字符串或字符串列表。
        cols:    终端宽度（列数），默认 80。
        rows:    终端高度（行数），默认 24。
        shell:   指定解释器（cmd/powershell/pwsh/bash），默认 powershell（不可用回退 cmd）。

    Returns:
        PseudoTerminal 子类实例。

    Raises:
        RuntimeError: 所有后端均创建失败时抛出。
    """
    # 注意: 字符串命令走 Subprocess（无 TTY）。ConPTY 后端在 CreatePseudoConsole
    # 时有挂起风险（Windows 版本兼容问题），列表命令会尝试 ConPTY。
    if isinstance(command, str):
        cmd_preview = command[:200] if isinstance(command, str) else command
        _logger.info("create_pty: string command, using Subprocess shell=%s cmd=%r", shell, cmd_preview)
        return SubprocessPseudoTerminal(command, cols, rows, shell=shell)

    if IS_WINDOWS:
        if _CONDRV_OK:
            try:
                _logger.info("create_pty: trying ConDrvPseudoTerminal")
                return ConDrvPseudoTerminal(command, cols, rows)
            except Exception as e:
                _logger.warning("create_pty: ConDrvPseudoTerminal failed: %s, falling back", e)
        try:
            _logger.info("create_pty: trying WindowsPseudoTerminal (ConPTY)")
            return WindowsPseudoTerminal(command, cols, rows)
        except Exception as e:
            _logger.warning("create_pty: WindowsPseudoTerminal failed: %s, falling back to Subprocess", e)
        _logger.info("create_pty: fallback to SubprocessPseudoTerminal")
        return SubprocessPseudoTerminal(command, cols, rows)

    try:
        _logger.info("create_pty: trying UnixPseudoTerminal")
        return UnixPseudoTerminal(command, cols, rows)
    except Exception as e:
        _logger.warning("create_pty: UnixPseudoTerminal failed: %s, falling back to Subprocess", e)
        return SubprocessPseudoTerminal(command, cols, rows)
