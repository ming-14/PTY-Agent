"""测试 WriteConsoleInputW 是否能向 ConPTY 子进程控制台注入事件

向 gdu 进程注入 KEY_EVENT_RECORD('j')，如果 gdu 选中项下移，说明 WriteConsoleInputW 工作。
"""
import ctypes
import sys
import time
from ctypes import wintypes as W

# Windows API 常量
STD_INPUT_HANDLE = -10
KEY_EVENT = 0x0001
MOUSE_EVENT = 0x0002
ENABLE_MOUSE_INPUT = 0x0010
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

# Windows API
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

_FreeConsole = kernel32.FreeConsole
_FreeConsole.argtypes = []
_FreeConsole.restype = W.BOOL

_AttachConsole = kernel32.AttachConsole
_AttachConsole.argtypes = [W.DWORD]
_AttachConsole.restype = W.BOOL

_CreateFileW = kernel32.CreateFileW
_CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE]
_CreateFileW.restype = W.HANDLE

_CloseHandle = kernel32.CloseHandle
_CloseHandle.argtypes = [W.HANDLE]
_CloseHandle.restype = W.BOOL

_WriteConsoleInputW = kernel32.WriteConsoleInputW
_WriteConsoleInputW.argtypes = [W.HANDLE, W.LPVOID, W.DWORD, W.LPDWORD]
_WriteConsoleInputW.restype = W.BOOL

_GetConsoleMode = kernel32.GetConsoleMode
_GetConsoleMode.argtypes = [W.HANDLE, W.LPDWORD]
_GetConsoleMode.restype = W.BOOL

_SetConsoleMode = kernel32.SetConsoleMode
_SetConsoleMode.argtypes = [W.HANDLE, W.DWORD]
_SetConsoleMode.restype = W.BOOL

_GetNumberOfConsoleInputEvents = kernel32.GetNumberOfConsoleInputEvents
_GetNumberOfConsoleInputEvents.argtypes = [W.HANDLE, W.LPDWORD]
_GetNumberOfConsoleInputEvents.restype = W.BOOL


class _KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", W.BOOL),
        ("wRepeatCount", W.WORD),
        ("wVirtualKeyCode", W.WORD),
        ("wVirtualScanCode", W.WORD),
        ("uChar", W.WCHAR),
        ("dwControlKeyState", W.DWORD),
    ]


class _COORD(ctypes.Structure):
    _fields_ = [("X", W.SHORT), ("Y", W.SHORT)]


class _MOUSE_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("dwMousePosition", _COORD),
        ("dwButtonState", W.DWORD),
        ("dwControlKeyState", W.DWORD),
        ("dwEventFlags", W.DWORD),
    ]


class _INPUT_RECORD(ctypes.Structure):
    class _Event(ctypes.Union):
        _fields_ = [
            ("KeyEvent", _KEY_EVENT_RECORD),
            ("MouseEvent", _MOUSE_EVENT_RECORD),
        ]
    _anonymous_ = ("_Event",)
    _fields_ = [
        ("EventType", W.WORD),
        ("_Event", _Event),
    ]


def inject_key(pid: int, ch: str, vk: int = 0):
    """注入一个 KEY_EVENT_RECORD (key down + key up)

    重要：不要 print 任何输出！AttachConsole 会重定向 stdout 到子进程控制台，
    print 输出会污染子进程的输出流。
    """
    _FreeConsole()
    if not _AttachConsole(pid):
        return False

    hConIn = _CreateFileW(
        "CONIN$",
        0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
        1 | 2,  # FILE_SHARE_READ | FILE_SHARE_WRITE
        None,
        3,  # OPEN_EXISTING
        0,
        None,
    )
    if not hConIn or hConIn == W.HANDLE(-1):
        return False

    try:
        # 检查写入前的事件数量
        before = W.DWORD(0)
        _GetNumberOfConsoleInputEvents(hConIn, ctypes.byref(before))

        # 构造 key down + key up
        records = (_INPUT_RECORD * 2)()
        records[0].EventType = KEY_EVENT
        records[0].KeyEvent.bKeyDown = True
        records[0].KeyEvent.wRepeatCount = 1
        records[0].KeyEvent.wVirtualKeyCode = vk if vk else ord(ch.upper())
        records[0].KeyEvent.wVirtualScanCode = 0
        records[0].KeyEvent.uChar = ch
        records[0].KeyEvent.dwControlKeyState = 0

        records[1].EventType = KEY_EVENT
        records[1].KeyEvent.bKeyDown = False
        records[1].KeyEvent.wRepeatCount = 1
        records[1].KeyEvent.wVirtualKeyCode = vk if vk else ord(ch.upper())
        records[1].KeyEvent.wVirtualScanCode = 0
        records[1].KeyEvent.uChar = ch
        records[1].KeyEvent.dwControlKeyState = 0

        written = W.DWORD(0)
        ok = _WriteConsoleInputW(hConIn, records, 2, ctypes.byref(written))

        # 检查写入后的事件数量
        after = W.DWORD(0)
        _GetNumberOfConsoleInputEvents(hConIn, ctypes.byref(after))

        # 写入日志文件（不污染 stdout）
        import os
        log_path = os.path.join(os.path.dirname(__file__), "inject_key.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"KEY pid={pid} ch={ch!r} vk={vk} ok={ok} written={written.value} "
                    f"before={before.value} after={after.value} delta={after.value-before.value}\n")
        return bool(ok)
    finally:
        _CloseHandle(hConIn)
        _FreeConsole()


def inject_mouse(pid: int, x: int, y: int, button_state: int, event_flags: int):
    """注入一个 MOUSE_EVENT_RECORD"""
    _FreeConsole()
    if not _AttachConsole(pid):
        return False

    hConIn = _CreateFileW(
        "CONIN$",
        0x80000000 | 0x40000000,
        1 | 2,
        None,
        3,
        0,
        None,
    )
    if not hConIn or hConIn == W.HANDLE(-1):
        return False

    try:
        # 检查并设置 console mode
        mode = W.DWORD(0)
        if _GetConsoleMode(hConIn, ctypes.byref(mode)):
            new_mode = mode.value | ENABLE_MOUSE_INPUT | ENABLE_EXTENDED_FLAGS
            new_mode &= ~ENABLE_QUICK_EDIT_MODE
            new_mode &= ~ENABLE_VIRTUAL_TERMINAL_INPUT
            if new_mode != mode.value:
                _SetConsoleMode(hConIn, new_mode)

        # 检查写入前的事件数量
        before = W.DWORD(0)
        _GetNumberOfConsoleInputEvents(hConIn, ctypes.byref(before))

        # 构造 MOUSE_EVENT_RECORD
        rec = _INPUT_RECORD()
        rec.EventType = MOUSE_EVENT
        rec.MouseEvent.dwMousePosition.X = x
        rec.MouseEvent.dwMousePosition.Y = y
        rec.MouseEvent.dwButtonState = button_state
        rec.MouseEvent.dwControlKeyState = 0
        rec.MouseEvent.dwEventFlags = event_flags

        written = W.DWORD(0)
        ok = _WriteConsoleInputW(hConIn, ctypes.byref(rec), 1, ctypes.byref(written))

        # 检查写入后的事件数量
        after = W.DWORD(0)
        _GetNumberOfConsoleInputEvents(hConIn, ctypes.byref(after))

        # 等待一下，看事件是否被读取
        time.sleep(0.2)
        final = W.DWORD(0)
        _GetNumberOfConsoleInputEvents(hConIn, ctypes.byref(final))

        # 写入日志文件
        import os
        log_path = os.path.join(os.path.dirname(__file__), "inject_key.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"MOUSE pid={pid} pos=({x},{y}) btn=0x{button_state:x} flags=0x{event_flags:x} "
                    f"ok={ok} written={written.value} before={before.value} after={after.value} "
                    f"delta={after.value-before.value} final={final.value} consumed={after.value-final.value}\n")
        return bool(ok)
    finally:
        _CloseHandle(hConIn)
        _FreeConsole()


if __name__ == "__main__":
    pid = int(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else "key"

    if mode == "key":
        # 注入 'j' 字符
        inject_key(pid, "j", vk=0x4A)  # VK=0x4A 是 'J'
    elif mode == "mouse":
        # 注入鼠标左键按下 + 释放 (10, 5) - 0-based
        FROM_LEFT_1ST_BUTTON_PRESSED = 0x0001
        inject_mouse(pid, 10, 5, FROM_LEFT_1ST_BUTTON_PRESSED, 0)
        time.sleep(0.05)
        inject_mouse(pid, 10, 5, 0, 0)
    elif mode == "wheel":
        # 注入滚轮向上 (WHEEL_DELTA=120, 高字)
        MOUSE_WHEELED = 0x0004
        WHEEL_DELTA = 120
        inject_mouse(pid, 10, 5, (WHEEL_DELTA << 16), MOUSE_WHEELED)

