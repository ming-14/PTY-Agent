"""测试通过 WriteConsoleInputW 注入键盘事件到 gdu 进程。

如果 gdu 响应了这个注入的键盘事件（选中行移动），则说明：
- AttachConsole 路径工作正常
- WriteConsoleInputW 写入的 KEY_EVENT_RECORD 能被 tcell 的 scanInput 读取

如果 gdu 不响应，则说明：
- AttachConsole 没有附加到正确的控制台
- 或者 ConPTY 模式下 conhost 拦截了写入的事件
"""

import ctypes
import ctypes.wintypes as W
import os
import sys
import time

# 加载 Windows API
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = W.HANDLE(-1).value

# Console mode flags
ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004
ENABLE_WINDOW_INPUT = 0x0008
ENABLE_MOUSE_INPUT = 0x0010
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

# Event types
KEY_EVENT = 0x0001
MOUSE_EVENT = 0x0002

# API prototypes
k32.FreeConsole.argtypes = []
k32.FreeConsole.restype = W.BOOL

k32.AttachConsole.argtypes = [W.DWORD]
k32.AttachConsole.restype = W.BOOL

k32.CreateFileW.argtypes = [
    W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p,
    W.DWORD, W.DWORD, ctypes.c_void_p
]
k32.CreateFileW.restype = W.HANDLE

k32.CloseHandle.argtypes = [W.HANDLE]
k32.CloseHandle.restype = W.BOOL

k32.GetConsoleMode.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
k32.GetConsoleMode.restype = W.BOOL

k32.SetConsoleMode.argtypes = [W.HANDLE, W.DWORD]
k32.SetConsoleMode.restype = W.BOOL


class _COORD(ctypes.Structure):
    _fields_ = [("X", W.SHORT), ("Y", W.SHORT)]


class _KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", W.BOOL),
        ("wRepeatCount", W.WORD),
        ("wVirtualKeyCode", W.WORD),
        ("wVirtualScanCode", W.WORD),
        ("UnicodeChar", W.WCHAR),
        ("dwControlKeyState", W.DWORD),
    ]


class _MOUSE_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("dwMousePosition", _COORD),
        ("dwButtonState", W.DWORD),
        ("dwControlKeyState", W.DWORD),
        ("dwEventFlags", W.DWORD),
    ]


class _INPUT_RECORD_EVENT(ctypes.Union):
    _fields_ = [
        ("KeyEvent", _KEY_EVENT_RECORD),
        ("MouseEvent", _MOUSE_EVENT_RECORD),
    ]


class _INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", W.WORD), ("Event", _INPUT_RECORD_EVENT)]


k32.WriteConsoleInputW.argtypes = [
    W.HANDLE, ctypes.POINTER(_INPUT_RECORD), W.DWORD, ctypes.POINTER(W.DWORD)
]
k32.WriteConsoleInputW.restype = W.BOOL


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open("test_inject_keyboard.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def inject_keyboard(pid, key_char):
    """注入一个按键（按下 + 释放）到目标进程的控制台输入缓冲区"""
    log(f"=== inject_keyboard pid={pid} key={key_char!r} ===")

    # FreeConsole + AttachConsole
    ok = k32.FreeConsole()
    log(f"FreeConsole: ok={ok} err={ctypes.get_last_error()}")

    ok = k32.AttachConsole(pid)
    err = ctypes.get_last_error()
    log(f"AttachConsole({pid}): ok={ok} err={err}")
    if not ok:
        return False

    try:
        hConIn = k32.CreateFileW(
            "CONIN$", GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
        )
        log(f"CreateFileW(CONIN$): h={hConIn:#x} err={ctypes.get_last_error()}")
        if not hConIn or hConIn == INVALID_HANDLE_VALUE:
            return False

        # 查看当前模式
        mode = W.DWORD()
        k32.GetConsoleMode(hConIn, ctypes.byref(mode))
        log(f"current CONIN$ mode: 0x{mode.value:x}")

        # 构造 KEY_EVENT_RECORD：按下 + 释放
        byte_val = ord(key_char)
        records = []
        for key_down in (True, False):
            rec = _INPUT_RECORD()
            rec.EventType = KEY_EVENT
            ke = rec.Event.KeyEvent
            ke.bKeyDown = key_down
            ke.wRepeatCount = 1
            ke.wVirtualKeyCode = byte_val if byte_val < 0x80 else 0
            ke.wVirtualScanCode = 0
            ke.UnicodeChar = key_char
            ke.dwControlKeyState = 0
            records.append(rec)

        buf = (_INPUT_RECORD * len(records))(*records)
        written = W.DWORD()
        ok = k32.WriteConsoleInputW(hConIn, buf, len(records), ctypes.byref(written))
        log(f"WriteConsoleInputW: ok={ok} written={written.value} err={ctypes.get_last_error()}")

        # 等待 tcell scanInput 读取
        time.sleep(0.1)

        k32.CloseHandle(hConIn)
        return bool(ok)
    finally:
        k32.FreeConsole()


def inject_mouse(pid, x, y):
    """注入一个鼠标 click（按下 + 释放）到目标进程的控制台输入缓冲区"""
    log(f"=== inject_mouse pid={pid} pos=({x},{y}) ===")

    ok = k32.FreeConsole()
    log(f"FreeConsole: ok={ok}")

    ok = k32.AttachConsole(pid)
    log(f"AttachConsole({pid}): ok={ok} err={ctypes.get_last_error()}")
    if not ok:
        return False

    try:
        hConIn = k32.CreateFileW(
            "CONIN$", GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
        )
        log(f"CreateFileW(CONIN$): h={hConIn:#x}")
        if not hConIn or hConIn == INVALID_HANDLE_VALUE:
            return False

        mode = W.DWORD()
        k32.GetConsoleMode(hConIn, ctypes.byref(mode))
        log(f"current CONIN$ mode: 0x{mode.value:x}")

        # 尝试两种模式：
        # 1. 保留 VT_INPUT + 添加 MOUSE_INPUT = 0x298
        # 2. 去掉 VT_INPUT，只用 MOUSE_INPUT = 0x98
        test_modes = [
            ("VT_INPUT+MOUSE_INPUT (0x298)", mode.value | ENABLE_MOUSE_INPUT | ENABLE_EXTENDED_FLAGS),
            ("MOUSE_INPUT only (0x98)", ENABLE_MOUSE_INPUT | ENABLE_WINDOW_INPUT | ENABLE_EXTENDED_FLAGS),
        ]

        for desc, new_mode in test_modes:
            log(f"--- testing mode: {desc} ---")
            k32.SetConsoleMode(hConIn, new_mode)
            verify = W.DWORD()
            k32.GetConsoleMode(hConIn, ctypes.byref(verify))
            log(f"set mode 0x{new_mode:x}, verify=0x{verify.value:x}")

            # 构造 MOUSE_EVENT_RECORD：按下 + 释放
            records = []
            for is_release in (False, True):
                rec = _INPUT_RECORD()
                rec.EventType = MOUSE_EVENT
                me = rec.Event.MouseEvent
                me.dwMousePosition.X = x
                me.dwMousePosition.Y = y
                me.dwButtonState = 0 if is_release else 0x0001  # FROM_LEFT_1ST_BUTTON_PRESSED
                me.dwControlKeyState = 0
                me.dwEventFlags = 0
                records.append(rec)

            buf = (_INPUT_RECORD * len(records))(*records)
            written = W.DWORD()
            ok = k32.WriteConsoleInputW(hConIn, buf, len(records), ctypes.byref(written))
            log(f"WriteConsoleInputW: ok={ok} written={written.value}")
            time.sleep(0.2)

            # 恢复模式
            k32.SetConsoleMode(hConIn, mode.value)
            log(f"restored mode to 0x{mode.value:x}")

            # 间隔
            time.sleep(0.5)

        k32.CloseHandle(hConIn)
        return True
    finally:
        k32.FreeConsole()


def main():
    if len(sys.argv) < 3:
        print("Usage: python test_inject_keyboard.py <pid> <key|mouse>")
        print("  key: inject keyboard 'j'")
        print("  mouse x y: inject mouse click at (x,y)")
        sys.exit(1)

    pid = int(sys.argv[1])
    action = sys.argv[2]

    # 清空日志
    with open("test_inject_keyboard.log", "w", encoding="utf-8") as f:
        f.write("")

    if action == "key":
        key = sys.argv[3] if len(sys.argv) > 3 else "j"
        inject_keyboard(pid, key)
    elif action == "mouse":
        x = int(sys.argv[3])
        y = int(sys.argv[4])
        inject_mouse(pid, x, y)
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
