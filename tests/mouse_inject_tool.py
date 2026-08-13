"""测试 ConPTY 模式下 WriteConsoleInputW 注入鼠标事件是否被子进程读取"""
import sys
import time
import ctypes
from ctypes import wintypes as W

kernel32 = ctypes.windll.kernel32
kernel32.SetLastError(0)

# API 绑定
_FreeConsole = kernel32.FreeConsole
_FreeConsole.argtypes = []
_FreeConsole.restype = W.BOOL

_AttachConsole = kernel32.AttachConsole
_AttachConsole.argtypes = [W.DWORD]
_AttachConsole.restype = W.BOOL

_GetConsoleMode = kernel32.GetConsoleMode
_GetConsoleMode.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
_GetConsoleMode.restype = W.BOOL

_SetConsoleMode = kernel32.SetConsoleMode
_SetConsoleMode.argtypes = [W.HANDLE, W.DWORD]
_SetConsoleMode.restype = W.BOOL

_CreateFileW = kernel32.CreateFileW
_CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
_CreateFileW.restype = W.HANDLE

_WriteConsoleInputW = kernel32.WriteConsoleInputW
_WriteConsoleInputW.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)]
_WriteConsoleInputW.restype = W.BOOL

_CloseHandle = kernel32.CloseHandle
_CloseHandle.argtypes = [W.HANDLE]
_CloseHandle.restype = W.BOOL

_GetNumberOfConsoleInputEvents = kernel32.GetNumberOfConsoleInputEvents
_GetNumberOfConsoleInputEvents.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
_GetNumberOfConsoleInputEvents.restype = W.BOOL

_PeekConsoleInputW = kernel32.PeekConsoleInputW
_PeekConsoleInputW.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)]
_PeekConsoleInputW.restype = W.BOOL

# 常量
ENABLE_MOUSE_INPUT = 0x0010
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_QUICK_EDIT_MODE = 0x0040
MOUSE_EVENT = 0x0002
FROM_LEFT_1ST_BUTTON_PRESSED = 0x0001
DOUBLE_CLICK = 0x0002

class _COORD(ctypes.Structure):
    _fields_ = [("X", W.SHORT), ("Y", W.SHORT)]

class _MOUSE_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("dwMousePosition", _COORD),
        ("dwButtonState", W.DWORD),
        ("dwControlKeyState", W.DWORD),
        ("dwEventFlags", W.DWORD),
    ]

class _INPUT_RECORD_EVENT(ctypes.Union):
    _fields_ = [("KeyEvent", ctypes.c_byte * 16), ("MouseEvent", _MOUSE_EVENT_RECORD)]

class _INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", W.WORD), ("Event", _INPUT_RECORD_EVENT)]

def build_mouse_record(x, y, button_state, event_flags):
    rec = _INPUT_RECORD()
    rec.EventType = MOUSE_EVENT
    me = ctypes.cast(ctypes.byref(rec.Event), ctypes.POINTER(_MOUSE_EVENT_RECORD)).contents
    me.dwMousePosition.X = x
    me.dwMousePosition.Y = y
    me.dwButtonState = button_state
    me.dwControlKeyState = 0
    me.dwEventFlags = event_flags
    return rec

def test_inject(pid):
    print(f"Testing mouse injection for pid={pid}")
    
    _FreeConsole()
    if not _AttachConsole(pid):
        print(f"AttachConsole failed: {ctypes.get_last_error()}")
        return
    
    hConIn = _CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
    if not hConIn or hConIn == W.HANDLE(-1):
        print(f"CreateFile CONIN$ failed: {ctypes.get_last_error()}")
        _FreeConsole()
        return
    
    # 读取当前模式
    mode = W.DWORD()
    if _GetConsoleMode(hConIn, ctypes.byref(mode)):
        print(f"Current mode: 0x{mode.value:08x}")
        print(f"  ENABLE_MOUSE_INPUT: {bool(mode.value & ENABLE_MOUSE_INPUT)}")
    
    # 设置模式：添加 ENABLE_MOUSE_INPUT
    new_mode = mode.value | ENABLE_MOUSE_INPUT | ENABLE_EXTENDED_FLAGS
    new_mode &= ~ENABLE_QUICK_EDIT_MODE
    if _SetConsoleMode(hConIn, new_mode):
        print(f"Set mode to 0x{new_mode:08x}")
    else:
        print(f"SetConsoleMode failed: {ctypes.get_last_error()}")
    
    # 检查当前缓冲区中的事件数
    count = W.DWORD()
    _GetNumberOfConsoleInputEvents(hConIn, ctypes.byref(count))
    print(f"Events in buffer before injection: {count.value}")
    
    # 注入鼠标事件：左键按下 + 释放
    records = [
        build_mouse_record(24, 3, FROM_LEFT_1ST_BUTTON_PRESSED, 0),  # press
        build_mouse_record(24, 3, 0, 0),  # release
    ]
    buf = (_INPUT_RECORD * len(records))(*records)
    written = W.DWORD()
    ok = _WriteConsoleInputW(hConIn, buf, len(records), ctypes.byref(written))
    print(f"WriteConsoleInputW: ok={ok} written={written.value}")
    
    # 检查注入后的事件数
    _GetNumberOfConsoleInputEvents(hConIn, ctypes.byref(count))
    print(f"Events in buffer after injection: {count.value}")
    
    # 读取事件看看
    peek_buf = (_INPUT_RECORD * 20)()
    peek_read = W.DWORD()
    if _PeekConsoleInputW(hConIn, peek_buf, 20, ctypes.byref(peek_read)):
        print(f"PeekConsoleInput: {peek_read.value} events")
        for i in range(min(peek_read.value, 10)):
            rec = peek_buf[i]
            if rec.EventType == MOUSE_EVENT:
                me = ctypes.cast(ctypes.byref(rec.Event), ctypes.POINTER(_MOUSE_EVENT_RECORD)).contents
                print(f"  [{i}] MOUSE pos=({me.dwMousePosition.X},{me.dwMousePosition.Y}) "
                      f"state=0x{me.dwButtonState:x} flags=0x{me.dwEventFlags:x}")
            elif rec.EventType == 0x0001:
                print(f"  [{i}] KEY")
            else:
                print(f"  [{i}] type=0x{rec.EventType:x}")
    
    # 等待一下
    time.sleep(0.2)
    
    # 不恢复模式
    _CloseHandle(hConIn)
    _FreeConsole()
    print("Done - mode NOT restored (ENABLE_MOUSE_INPUT stays on)")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <pid>")
        sys.exit(1)
    test_inject(int(sys.argv[1]))
