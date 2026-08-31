"""跨平台进程退出码 / 错误格式化（统一抽象）

所有 PTY 后端（Windows / Unix / subprocess）共用的退出码语义：

- STILL_ACTIVE：Windows 约定 259 表示"进程仍在运行"。
  Unix 后端不使用该值（waitpid 返回 None 表示仍在运行），
  但 ProcessMonitor 统一使用该常量避免平台差异。
- format_exit_code()：把任意退出码格式化为人类可读描述。
  Windows 上自动附加 NTSTATUS / Win32 错误名（委托 windows.error_msg），
  Unix 上仅做数字格式化。

职责边界：
- 本模块不依赖任何平台 API，可在所有平台导入。
- Windows 特有错误表（NTSTATUS 名称等）保留在 windows/error_msg.py。
"""

import logging
import sys

_logger = logging.getLogger("pty-errors")

# Windows 约定：GetExitCodeProcess 返回 259 表示进程仍在运行。
# 该值在 Windows 上使用；Unix 后端 waitpid 返回 None 表示运行中，
# 但 ProcessMonitor 用同一常量做判断，保持两平台逻辑一致。
STILL_ACTIVE = 259

# ── Windows 错误格式化（延迟加载，仅 Windows 平台）──
_format_exit_code_impl = None
_translate_impl = None
_loading_failed = False  # 缓存失败，避免重复尝试


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
    Unix 上返回通用的信号/退出码描述。

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
    # Unix 通用描述：负值表示信号终止
    if isinstance(exit_code, int) and exit_code < 0:
        return f"terminated by signal {-exit_code}"
    unsigned = exit_code & 0xFFFFFFFF if isinstance(exit_code, int) else exit_code
    if unsigned >= 0x80000000:
        return f"abnormal exit (0x{unsigned:08X})"
    return f"exit code {exit_code}"


def format_exit_code(exit_code) -> str:
    """跨平台格式化进程退出码

    Args:
        exit_code: 退出码（int）或 None（进程仍在运行）。

    Returns:
        人类可读的退出描述字符串。
    """
    if exit_code is None:
        return "process still running"
    if _format_exit_code_impl is None and not _loading_failed:
        _load_windows_formatter()
    if _format_exit_code_impl is not None:
        try:
            return _format_exit_code_impl(exit_code)
        except Exception as e:
            _logger.debug("Windows 退出码格式化失败: %s", e)
    # Unix 通用描述
    unsigned = exit_code & 0xFFFFFFFF
    if unsigned >= 0x80000000:
        return (f"process exited abnormally (exit={exit_code}, "
                f"0x{unsigned:08X})")
    return f"process exited abnormally (exit={exit_code})"
