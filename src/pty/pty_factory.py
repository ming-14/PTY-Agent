"""伪终端后端层 — 工厂函数与平台检测

提供 create_pty() 工厂函数，按优先级尝试各 PTY 后端实现。

后端优先级（Windows）:
  wezterm-py（OpenConsole 宿主，唯一后端）> 沙箱（winsandbox，sandbox.toml
  enabled=true 时）
Unix 统一使用 wezterm-py（portable-pty openpty）。
工厂入口统一归一化命令：str 按 shell 语义拆分（shlex.split），后端统一消费 List[str]。
"""

import shlex
from typing import List, Optional

from ..config.common import DEFAULT_COLS, DEFAULT_ROWS, IS_WINDOWS
from ..process.base import ProcessTreeTracker
from .base import PseudoTerminal
from .wezterm_pty import _HAS_WEZTERM, WeztermPseudoTerminal
from ..logging import get_logger

_logger = get_logger("pty-factory")


def create_pty(
    command: List[str],
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    encoding: Optional[str] = None,
    tracker: Optional[ProcessTreeTracker] = None,
) -> PseudoTerminal:
    """创建最优可用的 PTY 后端实例

    所有平台统一优先 wezterm-py（Windows: OpenConsole 宿主；
    Unix: portable-pty openpty）；Windows 沙箱启用时走沙箱后端。

    Args:
        command:  命令（List[str] 或 str；str 时按 shell 语义拆分）。
        cols:     终端宽度（列数），默认 {DEFAULT_COLS}。
        rows:     终端高度（行数），默认 {DEFAULT_ROWS}。
        cwd:      子进程工作目录（默认守护进程当前目录）。
        env:      子进程额外环境变量（dict，合并到 os.environ）。
        encoding: 终端输出编码（Windows 影响 ConPTY 代码页设置）。
        tracker:  Session 创建的进程树追踪器（spawn 成功后同一路径内
                  register_root，进程树归属 tracker）。

    Returns:
        PseudoTerminal 子类实例。

    Raises:
        RuntimeError: 所有 PTY 后端均创建失败时抛出。
    """
    # 命令归一化：str 按 shell 语义拆分（Windows 下用 cmd 兼容拆分），
    # 各后端均要求 List[str]，避免逐字符展开
    if isinstance(command, str):
        command = shlex.split(command)
    if IS_WINDOWS:
        # 首选 wezterm-py（OpenConsole 宿主，规避系统 conhost 的 VT 输入缺陷）
        if _HAS_WEZTERM:
            try:
                _logger.info("create_pty: trying WeztermPseudoTerminal")
                return WeztermPseudoTerminal(
                    command,
                    cols,
                    rows,
                    cwd=cwd,
                    env=env,
                    encoding=encoding,
                    tracker=tracker,
                )
            except FileNotFoundError:
                # 命令不存在（如可执行文件缺失）：不是 PTY 后端问题，
                # 直接透传让上层给出"命令不可执行"的准确错误，不尝试其他后端
                raise
            except Exception as e:
                _logger.warning(
                    "create_pty: WeztermPseudoTerminal failed: %s, falling back", e
                )
        sbx_pty = _try_create_sandbox_pty(
            command, cols, rows, cwd, env, encoding, tracker
        )
        if sbx_pty is not None:
            return sbx_pty
        raise RuntimeError("所有 PTY 后端均创建失败（wezterm-py 不可用或创建失败）")

    # Unix：统一 wezterm-py（portable-pty openpty）
    if _HAS_WEZTERM:
        try:
            _logger.info("create_pty: trying WeztermPseudoTerminal (unix)")
            return WeztermPseudoTerminal(
                command,
                cols,
                rows,
                cwd=cwd,
                env=env,
                encoding=encoding,
                tracker=tracker,
            )
        except Exception as e:
            raise RuntimeError(f"WeztermPseudoTerminal 创建失败: {e}") from e
    raise RuntimeError("wezterm-py 不可用，无法创建 PTY 后端")


def _try_create_sandbox_pty(
    command, cols, rows, cwd, env, encoding, tracker: Optional[ProcessTreeTracker]
):
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
    from ..sandbox import SandboxProcessTreeTracker, SandboxPty

    if tracker is None:
        # 无进程树追踪需求：视为非沙箱会话，回退原生后端
        return None
    if not isinstance(tracker, SandboxProcessTreeTracker):
        # 配置不一致（理论上不会发生：Session 工厂同受 ENABLED 控制）
        _logger.error("sandbox.enabled=true 但 tracker 不是 SandboxProcessTreeTracker")
        raise RuntimeError("沙箱已启用但进程树追踪器类型不匹配")
    try:
        _logger.info("create_pty: trying SandboxPty (winsandbox)")
        return SandboxPty(
            command,
            cols,
            rows,
            cwd=cwd,
            env=env,
            encoding=encoding,
            tracker=tracker,
            manager=tracker.manager,
        )
    except Exception as e:
        raise RuntimeError(f"沙箱 PTY 创建失败（沙箱启用时不回退原生后端）: {e}") from e
