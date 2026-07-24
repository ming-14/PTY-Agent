"""伪终端后端层 — 工厂函数与平台检测

提供 create_pty() 工厂函数，按优先级尝试各 PTY 后端实现。
Windows 特有代码存放在 windows/ 子包下，Unix 平台零加载。

注意：subprocess 管道模式已移除，仅保留真正的伪终端后端。
调用方必须传入已拆分的命令列表（通过 shlex.split）。
"""

import logging
from typing import Optional, List
from ..config.common import IS_WINDOWS, DEFAULT_COLS, DEFAULT_ROWS
from .base import PseudoTerminal
from .unix import UnixPseudoTerminal

_logger = logging.getLogger("pty-factory")

if IS_WINDOWS:
    from .windows.conpty import WindowsPseudoTerminal
    from .windows.condrv import ConDrvPseudoTerminal
    from .windows.win32_api import _CONDRV_OK


def create_pty(command: List[str], cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS,
               cwd: Optional[str] = None, env: Optional[dict] = None,
               encoding: Optional[str] = None) -> PseudoTerminal:
    """创建最优可用的 PTY 后端实例

    优先级:
      Windows: ConDrv 直连 > kernel32.CreatePseudoConsole
      Unix:    UnixPseudoTerminal

    Args:
        command:  已拆分的命令参数列表（调用方负责 shlex.split）。
        cols:     终端宽度（列数），默认 {DEFAULT_COLS}。
        rows:     终端高度（行数），默认 {DEFAULT_ROWS}。
        cwd:      子进程工作目录（默认守护进程当前目录）。
        env:      子进程额外环境变量（dict，合并到 os.environ）。
        encoding: 终端输出编码（影响 Windows ConPTY 代码页设置）。

    Returns:
        PseudoTerminal 子类实例。

    Raises:
        RuntimeError: 所有 PTY 后端均创建失败时抛出。
    """
    if IS_WINDOWS:
        if _CONDRV_OK:
            try:
                _logger.info("create_pty: trying ConDrvPseudoTerminal")
                return ConDrvPseudoTerminal(command, cols, rows, cwd=cwd, env=env)
            except Exception as e:
                _logger.warning("create_pty: ConDrvPseudoTerminal failed: %s, falling back", e)
        try:
            _logger.info("create_pty: trying WindowsPseudoTerminal (ConPTY)")
            return WindowsPseudoTerminal(command, cols, rows, cwd=cwd, env=env, encoding=encoding)
        except Exception as e:
            raise RuntimeError(f"所有 PTY 后端均创建失败: {e}") from e

    try:
        _logger.info("create_pty: trying UnixPseudoTerminal")
        return UnixPseudoTerminal(command, cols, rows, cwd=cwd, env=env)
    except Exception as e:
        raise RuntimeError(f"UnixPseudoTerminal 创建失败: {e}") from e
