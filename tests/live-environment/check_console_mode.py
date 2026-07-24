"""检查指定 PID 的控制台输入/输出模式"""
import sys
import ctypes
from ctypes import wintypes as W

ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004
ENABLE_WINDOW_INPUT = 0x0008
ENABLE_MOUSE_INPUT = 0x0010
ENABLE_INSERT_MODE = 0x0020
ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_AUTO_POSITION = 0x0100
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

ENABLE_PROCESSED_OUTPUT = 0x0001
ENABLE_WRAP_AT_EOL_OUTPUT = 0x0002
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
DISABLE_NEWLINE_AUTO_RETURN = 0x0008
ENABLE_LVB_GRID_WORLDWIDE = 0x0010

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2

K = ctypes.WinDLL('kernel32', use_last_error=True)

_AttachConsole = K.AttachConsole
_AttachConsole.argtypes = [W.DWORD]
_AttachConsole.restype = W.BOOL

_FreeConsole = K.FreeConsole
_FreeConsole.argtypes = []
_FreeConsole.restype = W.BOOL

_CreateFileW = K.CreateFileW
_CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.c_void_p]
_CreateFileW.restype = W.HANDLE

_GetConsoleMode = K.GetConsoleMode
_GetConsoleMode.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
_GetConsoleMode.restype = W.BOOL

_CloseHandle = K.CloseHandle
_CloseHandle.argtypes = [W.HANDLE]
_CloseHandle.restype = W.BOOL


def describe_input_mode(mode):
    flags = []
    if mode & ENABLE_PROCESSED_INPUT: flags.append("PROCESSED_INPUT")
    if mode & ENABLE_LINE_INPUT: flags.append("LINE_INPUT")
    if mode & ENABLE_ECHO_INPUT: flags.append("ECHO_INPUT")
    if mode & ENABLE_WINDOW_INPUT: flags.append("WINDOW_INPUT")
    if mode & ENABLE_MOUSE_INPUT: flags.append("MOUSE_INPUT")
    if mode & ENABLE_INSERT_MODE: flags.append("INSERT_MODE")
    if mode & ENABLE_QUICK_EDIT_MODE: flags.append("QUICK_EDIT_MODE")
    if mode & ENABLE_EXTENDED_FLAGS: flags.append("EXTENDED_FLAGS")
    if mode & ENABLE_AUTO_POSITION: flags.append("AUTO_POSITION")
    if mode & ENABLE_VIRTUAL_TERMINAL_INPUT: flags.append("VT_INPUT")
    return flags


def describe_output_mode(mode):
    flags = []
    if mode & ENABLE_PROCESSED_OUTPUT: flags.append("PROCESSED_OUTPUT")
    if mode & ENABLE_WRAP_AT_EOL_OUTPUT: flags.append("WRAP_AT_EOL_OUTPUT")
    if mode & ENABLE_VIRTUAL_TERMINAL_PROCESSING: flags.append("VT_PROCESSING")
    if mode & DISABLE_NEWLINE_AUTO_RETURN: flags.append("DISABLE_NEWLINE_AUTO_RETURN")
    if mode & ENABLE_LVB_GRID_WORLDWIDE: flags.append("LVB_GRID_WORLDWIDE")
    return flags


def check_pid(pid, out_lines):
    out_lines.append(f"=== Checking PID {pid} ===")
    _FreeConsole()
    if not _AttachConsole(pid):
        err = ctypes.get_last_error()
        out_lines.append(f"AttachConsole({pid}) failed err={err}")
        return

    try:
        # CONIN$
        hConIn = _CreateFileW("CONIN$", GENERIC_READ | GENERIC_WRITE,
                              FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
        if hConIn and hConIn != W.HANDLE(-1):
            mode = W.DWORD()
            if _GetConsoleMode(hConIn, ctypes.byref(mode)):
                out_lines.append(f"CONIN$ mode = 0x{mode.value:x}")
                out_lines.append(f"  Input flags: {describe_input_mode(mode.value)}")
            _CloseHandle(hConIn)

        # CONOUT$
        hConOut = _CreateFileW("CONOUT$", GENERIC_READ | GENERIC_WRITE,
                               FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
        if hConOut and hConOut != W.HANDLE(-1):
            mode = W.DWORD()
            if _GetConsoleMode(hConOut, ctypes.byref(mode)):
                out_lines.append(f"CONOUT$ mode = 0x{mode.value:x}")
                out_lines.append(f"  Output flags: {describe_output_mode(mode.value)}")
            _CloseHandle(hConOut)
    finally:
        _FreeConsole()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python check_console_mode.py <pid>")
        sys.exit(1)
    out_lines = []
    check_pid(int(sys.argv[1]), out_lines)
    # Reattach to our own console (parent process) for output
    try:
        _AttachConsole(ctypes.windll.kernel32.GetCurrentProcessId())
    except Exception:
        pass
    with open("check_console_mode_output.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
