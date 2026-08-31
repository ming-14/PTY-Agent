"""跨平台进程退出码 / 错误格式化（统一抽象）

所有 PTY 后端（Windows / Unix / subprocess）共用的退出码语义，以及
session 层退出码描述的**唯一实现**（info.py 委托本模块，不再重复）：

- STILL_ACTIVE：Windows 约定 259 表示"进程仍在运行"。
  Unix 后端不使用该值（waitpid 返回 None 表示仍在运行），
  但 ProcessMonitor 统一使用该常量避免平台差异。
- translate_exit_code()：简短描述（崩溃事件 info 使用）。
- format_exit_code_message()：完整描述（session error_message 使用），
  None/0 返回 None。
- signal_name()：Unix 信号编号 → 名称（如 SIGKILL）；Windows 恒返回
  SIGUNKNOWN(N)。

职责边界：
- 本模块不依赖任何平台 API，可在所有平台导入。
- Windows 特有错误表（NTSTATUS 名称等）保留在 windows/error_msg.py。
"""

import logging
import sys
from typing import Optional

_logger = logging.getLogger("pty-errors")

# Windows 约定：GetExitCodeProcess 返回 259 表示进程仍在运行。
# 该值在 Windows 上使用；Unix 后端 waitpid 返回 None 表示运行中，
# 但 ProcessMonitor 用同一常量做判断，保持两平台逻辑一致。
STILL_ACTIVE = 259

# ── Windows 错误格式化（延迟加载，仅 Windows 平台）──
_format_exit_code_impl = None
_translate_impl = None
_loading_failed = False  # 缓存失败，避免重复尝试


# ── 信号名称映射（仅 Unix 使用，延迟构建）──
_SIGNAL_NAMES: Optional[dict] = None


def _ensure_signal_names():
    """构建 Unix 信号编号 → 名称映射（首次调用时）"""
    global _SIGNAL_NAMES
    if _SIGNAL_NAMES is not None:
        return
    _SIGNAL_NAMES = {}
    try:
        import signal as _sig
        for name in dir(_sig):
            if name.startswith("SIG") and not name.startswith("SIG_"):
                val = getattr(_sig, name, None)
                if isinstance(val, int) and val != 0:
                    _SIGNAL_NAMES[val] = name
    except Exception:
        pass


def signal_name(signum: int) -> str:
    """将 Unix 信号编号转换为信号名称（如 9 → SIGKILL）

    Args:
        signum: 信号编号。

    Returns:
        信号名称字符串（如 "SIGKILL"）。未知信号返回 "SIGUNKNOWN(N)"。
    """
    _ensure_signal_names()
    return _SIGNAL_NAMES.get(signum, f"SIGUNKNOWN({signum})")


def _load_windows_formatter():
    """延迟加载 Windows 错误格式化实现（仅 Windows 平台）

    加载失败时缓存失败标志，避免在 Unix 上重复尝试。
    """
    global _format_exit_code_impl, _translate_impl, _loading_failed
    if _loading_failed:
        return False
    if sys.platform != "win32":
        _loading_failed = True
        return False
    try:
        from .windows.error_msg import (
            format_process_exit_code as _fmt,
            translate_windows_error as _tr,
        )
        _format_exit_code_impl = _fmt
        _translate_impl = _tr
        return True
    except Exception as e:
        _logger.debug("Windows 错误格式化不可用: %s", e)
        _loading_failed = True
        return False


def translate_exit_code(exit_code) -> str:
    """返回退出码的简短描述（供崩溃事件 info 使用）

    Windows 上返回 NTSTATUS / Win32 错误名描述；
    Unix 上返回附带信号名的描述。

    Args:
        exit_code: 退出码（int）。

    Returns:
        简短描述字符串。
    """
    if _translate_impl is None and not _loading_failed:
        _load_windows_formatter()
    if _translate_impl is not None:
        try:
            return _translate_impl(exit_code)
        except Exception as e:
            _logger.debug("Windows 退出码翻译失败: %s", e)
    # Unix 通用描述：负值表示信号终止，附带信号名
    if isinstance(exit_code, int) and exit_code < 0:
        sig = -exit_code
        return f"terminated by signal {signal_name(sig)} ({sig})"
    unsigned = exit_code & 0xFFFFFFFF if isinstance(exit_code, int) else exit_code
    if unsigned >= 0x80000000:
        return f"abnormal exit (0x{unsigned:08X})"
    return f"exit code {exit_code}"


def format_exit_code_message(exit_code) -> Optional[str]:
    """格式化进程退出码为可读的错误消息（session error_message 使用）

    None 或 0 返回 None（表示无错误）。
    Windows 上委托 format_process_exit_code（含 NTSTATUS/Win32 名称）；
    Unix 上对信号终止附加信号名描述。

    Args:
        exit_code: 退出码（int）或 None。

    Returns:
        可读的描述字符串；退出码为 None/0 时返回 None。
    """
    if exit_code is None or exit_code == 0:
        return None

    if _format_exit_code_impl is None and not _loading_failed:
        _load_windows_formatter()
    if _format_exit_code_impl is not None:
        try:
            return _format_exit_code_impl(exit_code)
        except Exception as e:
            _logger.debug("Windows 退出码格式化失败: %s", e)

    # Unix：信号终止（负值表示信号编号），附加信号名
    if isinstance(exit_code, int) and exit_code < 0:
        sig = -exit_code
        return f"进程被信号 {signal_name(sig)} ({sig}) 终止"
    # Unix：非零退出码
    return f"进程异常退出 (exit={exit_code})"