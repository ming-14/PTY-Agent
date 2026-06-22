"""Windows 进程错误码格式化

提供 Windows 错误码（Win32 错误码和 NTSTATUS 退出码）的格式化输出。

典型 NTSTATUS 退出码（RtlNtStatusToDosError）：
  0xC0000135 — STATUS_DLL_NOT_FOUND
  0xC0000142 — STATUS_DLL_INIT_FAILED
  0xC000007B — STATUS_BAD_IMAGE_FORMAT
  0xC0000139 — STATUS_ENTRYPOINT_NOT_FOUND
  0xC0000005 — STATUS_ACCESS_VIOLATION
"""

import sys
import logging

_logger = logging.getLogger("pty-windows-error")

# ── 在 Windows 上延迟加载 ctypes ──
_IS_WINDOWS = sys.platform == "win32"
_FORMAT_MSG_OK = False
_ctypes = None
_W = None

if _IS_WINDOWS:
    import ctypes as _ctypes
    from ctypes import wintypes as _W
    try:
        _KERNEL32 = _ctypes.WinDLL("kernel32", use_last_error=True)
        _FormatMessageW = _KERNEL32.FormatMessageW
        _FormatMessageW.restype = _W.DWORD
        _FormatMessageW.argtypes = [
            _W.DWORD, _ctypes.c_void_p, _W.DWORD, _W.DWORD,
            _ctypes.c_void_p, _W.DWORD, _ctypes.c_void_p,
        ]
        _FORMAT_MSG_OK = True
    except Exception:
        pass

# ── 常见 NTSTATUS 名称映射（仅作名称引用，不下发中文描述） ──
_NTSTATUS_NAMES = {
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC000001D: "STATUS_ILLEGAL_INSTRUCTION",
    0xC0000022: "STATUS_ACCESS_DENIED",
    0xC000007B: "STATUS_BAD_IMAGE_FORMAT",
    0xC000008F: "STATUS_FLOAT_MULTIPLE_FAULTS",
    0xC0000094: "STATUS_INTEGER_DIVIDE_BY_ZERO",
    0xC0000095: "STATUS_FLOAT_DIVIDE_BY_ZERO",
    0xC0000096: "STATUS_PRIVILEGED_INSTRUCTION",
    0xC00000FD: "STATUS_STACK_OVERFLOW",
    0xC0000135: "STATUS_DLL_NOT_FOUND",
    0xC0000139: "STATUS_ENTRYPOINT_NOT_FOUND",
    0xC0000142: "STATUS_DLL_INIT_FAILED",
    0xC000014B: "STATUS_DLL_INIT_FAILED_LOGOFF",
    0xC0000417: "STATUS_ASSERTION_FAILURE",
    0xC0000420: "STATUS_UNRECOGNIZED_VOLUME",
    0xC06D007E: "STATUS_MOD_NOT_FOUND",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
    0xC0000374: "STATUS_HEAP_CORRUPTION",
}

# ── 常见 Win32 错误码名称 ──
_WIN32_NAMES = {
    2:    "ERROR_FILE_NOT_FOUND",
    3:    "ERROR_PATH_NOT_FOUND",
    5:    "ERROR_ACCESS_DENIED",
    87:   "ERROR_INVALID_PARAMETER",
    126:  "ERROR_MOD_NOT_FOUND",
    193:  "ERROR_BAD_EXE_FORMAT",
    267:  "ERROR_DIRECTORY",
    740:  "ERROR_ELEVATION_REQUIRED",
    998:  "ERROR_NOACCESS",
    14001: "ERROR_SXS_CANT_GEN_ACTCTX",
    1450: "ERROR_NO_SYSTEM_RESOURCES",
}

# ── 常量 ──
FORMAT_MESSAGE_ALLOCATE_BUFFER = 0x00000100
FORMAT_MESSAGE_FROM_SYSTEM = 0x00001000
FORMAT_MESSAGE_IGNORE_INSERTS = 0x00000200

STILL_ACTIVE = 259  # STILL_ACTIVE (0x103)


def _try_format_message(error_code: int) -> str:
    """通过 Windows FormatMessageW 获取系统错误消息

    Args:
        error_code: Windows 错误码。

    Returns:
        系统错误消息字符串，获取失败返回空字符串。
    """
    if not _FORMAT_MSG_OK:
        return ""
    try:
        buf = _ctypes.c_void_p()
        n = _FormatMessageW(
            FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM
            | FORMAT_MESSAGE_IGNORE_INSERTS,
            None, error_code, 0,
            _ctypes.byref(buf), 0, None,
        )
        if n:
            ptr = _ctypes.cast(buf, _ctypes.POINTER(_W.WCHAR))
            result = ptr[:n]
            _ctypes.windll.kernel32.LocalFree(buf)
            text = "".join(result).strip().rstrip(".\r\n ")
            if text:
                return text
    except Exception:
        pass
    return ""


def translate_windows_error(error_code: int) -> str:
    """格式化 Windows 错误码为可读字符串

    优先使用内置名称表，回退到 FormatMessageW 获取系统消息。

    Args:
        error_code: Windows 错误码（Win32 或 NTSTATUS）。

    Returns:
        错误描述字符串（无匹配时返回通用描述）。
    """
    unsigned = error_code & 0xFFFFFFFF

    # 0 表示成功，非错误
    if error_code == 0:
        return ""

    # 检查 NTSTATUS 名称
    if unsigned in _NTSTATUS_NAMES:
        name = _NTSTATUS_NAMES[unsigned]
        return f"进程异常退出 ({name}, NTSTATUS=0x{unsigned:08X})"

    # 检查 Win32 错误码名称
    if error_code in _WIN32_NAMES:
        name = _WIN32_NAMES[error_code]
        return f"系统错误 ({name}, error={error_code})"

    # 尝试通过 FormatMessageW 获取系统消息
    if _FORMAT_MSG_OK and error_code & 0x80000000 == 0:
        sys_msg = _try_format_message(error_code)
        if sys_msg:
            return f"系统错误 ({sys_msg}, error={error_code})"

    # 通用描述
    if unsigned >= 0x80000000:
        return f"进程异常退出 (NTSTATUS=0x{unsigned:08X})"
    return f"系统错误 (error={error_code})"


def format_process_exit_code(exit_code: int) -> str:
    """格式化进程退出码为可读字符串

    Args:
        exit_code: 进程退出码。

    Returns:
        格式化的退出信息字符串。
    """
    if exit_code is None:
        return "process still running"
    if exit_code == 0:
        return "process exited normally (exit=0)"

    # 尝试格式化
    msg = translate_windows_error(exit_code)

    # 显示原始退出码
    unsigned = exit_code & 0xFFFFFFFF
    if unsigned >= 0x80000000:
        signed_str = str(exit_code)
        hex_str = f"0x{unsigned:08X}"
        return f"process exited abnormally (exit={signed_str}, {hex_str})\n{msg}"

    return f"process exited abnormally (exit={exit_code})\n{msg}"


def format_create_process_error(error_code: int) -> str:
    """格式化 CreateProcessW 失败的错误信息

    Args:
        error_code: GetLastError 返回的错误码。

    Returns:
        格式化的错误信息字符串。
    """
    msg = translate_windows_error(error_code)
    return f"create process failed (error={error_code}): {msg}"
