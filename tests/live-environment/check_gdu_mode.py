"""检查 gdu 子进程的 CONIN$ 实际模式"""
import ctypes
import sys
from ctypes import wintypes as W

# 重定向输出到文件，避免 FreeConsole 后 stdout 丢失
LOG_PATH = r"c:\Users\rikka\Desktop\PTY-Agent\logs\check_gdu_mode.log"
_log = open(LOG_PATH, "w", encoding="utf-8")
def log(msg):
    _log.write(str(msg) + "\n")
    _log.flush()
log(f"start, pid argv={sys.argv}")

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

AttachConsole = k32.AttachConsole
AttachConsole.restype = W.BOOL
AttachConsole.argtypes = [W.DWORD]

FreeConsole = k32.FreeConsole
FreeConsole.restype = W.BOOL
FreeConsole.argtypes = []

GetConsoleMode = k32.GetConsoleMode
GetConsoleMode.restype = W.BOOL
GetConsoleMode.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]

CreateFileW = k32.CreateFileW
CreateFileW.restype = W.HANDLE
CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]

CloseHandle = k32.CloseHandle
CloseHandle.restype = W.BOOL
CloseHandle.argtypes = [W.HANDLE]

pid = int(sys.argv[1]) if len(sys.argv) > 1 else 22368
log(f"Attaching to pid={pid}")

fc = FreeConsole()
log(f"FreeConsole returned {fc}, err={ctypes.get_last_error()}")

ac = AttachConsole(pid)
log(f"AttachConsole returned {ac}, err={ctypes.get_last_error()}")
if not ac:
    _log.close()
    sys.exit(1)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3

hConIn = CreateFileW(
    "CONIN$", GENERIC_READ | GENERIC_WRITE,
    FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None,
)
log(f"CreateFileW returned hConIn={hConIn}, err={ctypes.get_last_error()}")
INVALID = ctypes.c_void_p(-1).value or 0xFFFFFFFFFFFFFFFF
if not hConIn or hConIn == INVALID:
    err = ctypes.get_last_error()
    log(f"CreateFile CONIN$ failed err={err}")
    FreeConsole()
    _log.close()
    sys.exit(1)

mode = W.DWORD()
gcm = GetConsoleMode(hConIn, ctypes.byref(mode))
log(f"GetConsoleMode returned {gcm}, mode=0x{mode.value:x}, err={ctypes.get_last_error()}")

# 解析位
ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004
ENABLE_WINDOW_INPUT = 0x0008
ENABLE_MOUSE_INPUT = 0x0010
ENABLE_INSERT_MODE = 0x0020
ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
bits = {
    "PROCESSED_INPUT": ENABLE_PROCESSED_INPUT,
    "LINE_INPUT": ENABLE_LINE_INPUT,
    "ECHO_INPUT": ENABLE_ECHO_INPUT,
    "WINDOW_INPUT": ENABLE_WINDOW_INPUT,
    "MOUSE_INPUT": ENABLE_MOUSE_INPUT,
    "INSERT_MODE": ENABLE_INSERT_MODE,
    "QUICK_EDIT_MODE": ENABLE_QUICK_EDIT_MODE,
    "EXTENDED_FLAGS": ENABLE_EXTENDED_FLAGS,
    "VIRTUAL_TERMINAL_INPUT": ENABLE_VIRTUAL_TERMINAL_INPUT,
}
for name, val in bits.items():
    if mode.value & val:
        log(f"  - {name}")

CloseHandle(hConIn)
FreeConsole()
log("Done")
_log.close()
