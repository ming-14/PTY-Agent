"""伪终端后端层 — 工厂函数与平台检测

提供 create_pty() 工厂函数，按优先级尝试各 PTY 后端实现。
Windows 特有代码存放在 windows/ 子包下，Unix 平台零加载。

后端优先级（Windows）:
  沙箱（winsandbox，sandbox.toml enabled=true 时）> ConDrv 直连 > kernel32.CreatePseudoConsole
注意：subprocess 管道模式已移除，仅保留真正的伪终端后端。
工厂入口统一归一化命令：str 按 shell 语义拆分（shlex.split），后端统一消费 List[str]。
"""

import logging
import shlex
from typing import Optional, List
from ..config.common import IS_WINDOWS, DEFAULT_COLS, DEFAULT_ROWS
from ..process.base import ProcessTreeTracker
from .base import PseudoTerminal
from .unix import UnixPseudoTerminal

_logger = logging.getLogger("pty-factory")

if IS_WINDOWS:
    from .windows.conpty import WindowsPseudoTerminal
    from .windows.condrv import ConDrvPseudoTerminal
    from .windows.win32_api import _CONDRV_OK


def create_pty(command: List[str], cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS,
               cwd: Optional[str] = None, env: Optional[dict] = None,
               encoding: Optional[str] = None,
               tracker: Optional[ProcessTreeTracker] = None) -> PseudoTerminal:
    """创建最优可用的 PTY 后端实例

    优先级:
      Windows: 沙箱（winsandbox，sandbox.toml enabled=true）> ConDrv 直连 >
               kernel32.CreatePseudoConsole
      Unix:    UnixPseudoTerminal

    Args:
        command:  命令（List[str] 或 str；str 时按 shell 语义拆分）。
        cols:     终端宽度（列数），默认 {DEFAULT_COLS}。
        rows:     终端高度（行数），默认 {DEFAULT_ROWS}。
        cwd:      子进程工作目录（默认守护进程当前目录）。
        env:      子进程额外环境变量（dict，合并到 os.environ）。
        encoding: 终端输出编码（影响 Windows ConPTY 代码页设置）。
        tracker:  Session 创建的进程树追踪器（spawn 成功后同一路径内
                  register_root，进程树归属 tracker）。

    Returns:
        PseudoTerminal 子类实例。

    Raises:
        RuntimeError: 所有 PTY 后端均创建失败时抛出。
    """
    # 命令归一化：str 按 shell 语义拆分（Windows 下用 cmd 兼容拆分），
    # 各后端（list2cmdline/CreateProcess）均要求 List[str]，避免逐字符展开
    if isinstance(command, str):
        command = shlex.split(command)
    if IS_WINDOWS:
        sbx_pty = _try_create_sandbox_pty(command, cols, rows, cwd, env, encoding, tracker)
        if sbx_pty is not None:
            return sbx_pty
        if _CONDRV_OK:
            try:
                _logger.info("create_pty: trying ConDrvPseudoTerminal")
                return ConDrvPseudoTerminal(command, cols, rows, cwd=cwd, env=env, tracker=tracker)
            except Exception as e:
                _logger.warning("create_pty: ConDrvPseudoTerminal failed: %s, falling back", e)
        try:
            _logger.info("create_pty: trying WindowsPseudoTerminal (ConPTY)")
            return WindowsPseudoTerminal(command, cols, rows, cwd=cwd, env=env,
                                         encoding=encoding, tracker=tracker)
        except Exception as e:
            raise RuntimeError(f"所有 PTY 后端均创建失败: {e}") from e

    try:
        _logger.info("create_pty: trying UnixPseudoTerminal")
        return UnixPseudoTerminal(command, cols, rows, cwd=cwd, env=env, tracker=tracker)
    except Exception as e:
        raise RuntimeError(f"UnixPseudoTerminal 创建失败: {e}") from e


def _try_create_sandbox_pty(command, cols, rows, cwd, env, encoding,
                            tracker: Optional[ProcessTreeTracker]):
    """尝试创建 winsandbox 沙箱后端（sandbox.toml enabled=true 且带沙箱 tracker 时）

    沙箱会话 = 沙箱后端 + 沙箱进程树追踪器（Session 工厂同受 ENABLED 控制，
    保证成对出现）。未带 tracker（None）的裸后端调用（如单测直接调工厂）
    不构成沙箱会话，应走原生后端；带非沙箱 tracker 时视为配置不一致，
    强制报错（安全边界：开启沙箱不允许静默失去隔离）。

    Returns:
        PseudoTerminal 实例或 None（未启用沙箱 / 无沙箱 tracker 时）。
    """
    from ..config import sandbox as _sbx_cfg
    if not _sbx_cfg.ENABLED:
        return None
    from ..sandbox import SandboxPty, SandboxProcessTreeTracker
    if tracker is None:
        # 无进程树追踪需求：视为非沙箱会话，回退原生后端
        return None
    if not isinstance(tracker, SandboxProcessTreeTracker):
        # 配置不一致（理论上不会发生：Session 工厂同受 ENABLED 控制）
        _logger.error("sandbox.enabled=true 但 tracker 不是 SandboxProcessTreeTracker")
        raise RuntimeError("沙箱已启用但进程树追踪器类型不匹配")
    try:
        _logger.info("create_pty: trying SandboxPty (winsandbox)")
        return SandboxPty(command, cols, rows, cwd=cwd, env=env, encoding=encoding,
                          tracker=tracker, manager=tracker.manager)
    except Exception as e:
        raise RuntimeError(f"沙箱 PTY 创建失败（沙箱启用时不回退原生后端）: {e}") from e
