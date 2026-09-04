"""进程信息查询与错误消息格式化

提供按 PID 查询进程可执行文件名/路径的工具函数，
以及进程退出码和 PTY 创建失败的错误消息格式化。

退出码格式化统一委托 pty/errors.py（跨平台单一实现），
本模块仅保留 Windows 特有的 PTY 创建错误翻译。
"""

import os
import logging

from ...config import IS_WINDOWS
from ...pty.errors import format_exit_code_message as _format_exit_code_message
from ...pty.errors import signal_name as _signal_name

_logger = logging.getLogger("pty-session")


# ── 进程信息查询 ──


def _get_process_name(pid: int) -> str:
    """根据 PID 获取进程可执行文件名称（不含路径）

    Args:
        pid: 进程 ID。

    Returns:
        可执行文件名（如 g++.exe）。获取失败时返回 'PID {pid}'。
    """
    full = _get_process_path(pid)
    if full.startswith("PID "):
        return full
    if IS_WINDOWS:
        name = full.rsplit("\\", 1)[-1] if "\\" in full else full
    else:
        name = full.rsplit("/", 1)[-1] if "/" in full else full
    _logger.debug("get_process_name: pid=%d name=%s", pid, name)
    return name


def _get_process_path(pid: int) -> str:
    """根据 PID 获取进程可执行文件的完整路径

    Args:
        pid: 进程 ID。

    Returns:
        完整路径（如 C:\\Python311\\python.exe）。
        获取失败时返回 'PID {pid}'。
    """
    if IS_WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes as W
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            hproc = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not hproc:
                _logger.debug("get_process_path: OpenProcess(%d) failed", pid)
                return f"PID {pid}"
            try:
                buf = ctypes.create_unicode_buffer(260)
                size = W.DWORD(260)
                if k32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
                    _logger.debug("get_process_path: pid=%d path=%s", pid, buf.value)
                    return buf.value
                _logger.debug("get_process_path: QueryFullProcessImageNameW(%d) failed", pid)
                return f"PID {pid}"
            finally:
                k32.CloseHandle(hproc)
        except Exception as e:
            _logger.debug("get_process_path: pid=%d exception %s", pid, e)
            return f"PID {pid}"
    else:
        # Unix: 尝试读取 /proc/{pid}/exe 符号链接
        try:
            path = os.readlink(f"/proc/{pid}/exe")
            _logger.debug("get_process_path: pid=%d path=%s", pid, path)
            return path
        except Exception:
            pass
        # 回退到 comm
        try:
            with open(f"/proc/{pid}/comm", "r") as f:
                name = f.read().strip()
                _logger.debug("get_process_path: pid=%d comm=%s", pid, name)
                return name
        except Exception:
            _logger.debug("get_process_path: pid=%d not found", pid)
            return f"PID {pid}"


# ── 错误消息格式化 ──


def _format_pty_error(exception: Exception) -> str:
    """格式化 PTY 创建失败的异常为可读的错误消息

    在 Windows 上尝试翻译 OSError 中的错误码。

    Args:
        exception: PTY 创建时抛出的异常。

    Returns:
        可读的错误描述字符串。
    """
    if IS_WINDOWS and isinstance(exception, OSError) and exception.args:
        try:
            # OSError 格式：(error_code, message)
            if len(exception.args) >= 2 and isinstance(exception.args[0], int):
                from ...pty.errors import format_create_process_error
                return format_create_process_error(exception.args[0])
        except ImportError:
            pass
    return str(exception)
