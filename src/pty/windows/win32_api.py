"""Windows ConPTY 常量 / ctypes 类型 / API 函数绑定

集中管理所有 Windows API 声明，作为唯一的 API 声明文件。
仅在 Windows 平台被导入。

═══════════════════════════════════════════════════════════════
 ConPTY 正确用法（参考 Alacritty conpty.rs / Windows Terminal）
═══════════════════════════════════════════════════════════════

CreateProcessW 参数：
  - bInheritHandles = FALSE
    （伪控制台句柄通过 PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE 属性传递，
     不需要 bInheritHandles=TRUE。设 TRUE 反而会导致子进程继承父进程
     的管道句柄，使 isTTY 返回 false）

  - dwCreationFlags = EXTENDED_STARTUPINFO_PRESENT
    | [CREATE_UNICODE_ENVIRONMENT（有自定义环境时）]
    （不要用 CREATE_NO_WINDOW，ConPTY 内部以 --headless 模式启动
     conhost，不会弹出窗口；CREATE_NO_WINDOW 会阻止控制台创建，
     导致子进程 isTTY=false）

STARTUPINFOEXW 设置：
  - dwFlags = STARTF_USESTDHANDLES
  - hStdInput / hStdOutput / hStdError = NULL（保持为零）
    （设置 STARTF_USESTDHANDLES 但标准句柄留 NULL，确保子进程
     不继承父进程的任何句柄；ConPTY 内核驱动会自动为子进程
     分配伪控制台句柄，使 isTTY 返回 true）

UpdateProcThreadAttribute 关键注意事项：
  - dwAttribute = PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE (0x00020016)
  - lpValue = HPCON 值本身（c_void_p 实例），不是 &HPCON（byref）

    ⚠️ ctypes 陷阱：
    当 argtypes 声明 lpValue 为 c_void_p 时：
      ✅ 传 hpc（c_void_p 实例）→ ctypes 取 .value 作为指针值 → 正确
      ❌ 传 ctypes.byref(hpc) → 传递的是 Python 对象的内存地址 → 错误
         （子进程 isTTY=false，输出为 0 字节）

    原因：Windows API UpdateProcThreadAttribute 期望 lpValue
    指向包含属性值的内存。c_void_p 实例本身就是指针值（HPCON），
    ctypes 传 c_void_p 实例时会自动取 .value 传给 API，这正好
    就是指向 HPCON 值的指针。而 byref(hpc) 产生 CArgObject，
    ctypes 将其当作通用指针处理，传递了错误的地址。

CreatePseudoConsole：
  - 传入 conin 管道读端 + conout 管道写端
  - 管道需要设置 HANDLE_FLAG_INHERIT（供 conhost 使用）
  - 创建后关闭父进程中的 coninR / conoutW（conhost 已复制）

管道生命周期：
  - 父进程保留：coninW（写入子进程输入）、conoutR（读取子进程输出）
  - CreatePseudoConsole 后立即关闭：coninR、conoutW
  - close() 时关闭：coninW、conoutR、hpc（ClosePseudoConsole）
═══════════════════════════════════════════════════════════════
"""

import ctypes
from ctypes import wintypes as W

# ── DLL 句柄 ──
K = ctypes.WinDLL("kernel32", use_last_error=True)
N = ctypes.WinDLL("ntdll")
U = ctypes.WinDLL("user32", use_last_error=True)
D = None  # DbgHelp.dll — 延迟导入
_H_DBGHELP = None


# ============================================================
#  NT 类型定义
# ============================================================

class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length",        W.USHORT),
        ("MaximumLength", W.USHORT),
        ("Buffer",        W.LPWSTR),
    ]


class _OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Length",                   W.ULONG),
        ("RootDirectory",            W.HANDLE),
        ("ObjectName",               ctypes.POINTER(_UNICODE_STRING)),
        ("Attributes",               W.ULONG),
        ("SecurityDescriptor",       ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class _IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = [
        ("Status",      ctypes.c_void_p),
        ("Information", ctypes.c_void_p),
    ]


class _COORD(ctypes.Structure):
    _fields_ = [
        ("X", W.SHORT),
        ("Y", W.SHORT),
    ]


class _OVERLAPPED(ctypes.Structure):
    _is_64bit = ctypes.sizeof(ctypes.c_void_p) == 8
    _fields_ = [
        ("Internal",     ctypes.c_ulonglong if _is_64bit else ctypes.c_ulong),
        ("InternalHigh", ctypes.c_ulonglong if _is_64bit else ctypes.c_ulong),
        ("Offset",       W.DWORD),
        ("OffsetHigh",   W.DWORD),
        ("hEvent",       W.HANDLE),
    ]


class _SI(ctypes.Structure):
    _fields_ = [
        ("cb",              W.DWORD),
        ("lpReserved",      W.LPWSTR),
        ("lpDesktop",       W.LPWSTR),
        ("lpTitle",         W.LPWSTR),
        ("dwX",             W.DWORD),
        ("dwY",             W.DWORD),
        ("dwXSize",         W.DWORD),
        ("dwYSize",         W.DWORD),
        ("dwXCountChars",   W.DWORD),
        ("dwYCountChars",   W.DWORD),
        ("dwFillAttribute", W.DWORD),
        ("dwFlags",         W.DWORD),
        ("wShowWindow",     W.WORD),
        ("cbReserved2",     W.WORD),
        ("lpReserved2",     W.LPBYTE),
        ("hStdInput",       W.HANDLE),
        ("hStdOutput",      W.HANDLE),
        ("hStdError",       W.HANDLE),
    ]


class _SIE(ctypes.Structure):
    _fields_ = [
        ("StartupInfo",      _SI),
        ("lpAttributeList",  ctypes.c_void_p),
    ]


class _PI(ctypes.Structure):
    _fields_ = [
        ("hProcess",    W.HANDLE),
        ("hThread",     W.HANDLE),
        ("dwProcessId", W.DWORD),
        ("dwThreadId",  W.DWORD),
    ]


_HPCON = ctypes.c_void_p


class _PSEUDO_CONSOLE(ctypes.Structure):
    """伪 HPCON 结构体（ConDrv 直连路径）"""
    _fields_ = [
        ("hSignal",         W.HANDLE),
        ("hPtyReference",   W.HANDLE),
        ("hConPtyProcess",  W.HANDLE),
    ]


# ============================================================
#  API 绑定辅助
# ============================================================

def _api(name, restype, argtypes):
    """绑定 kernel32 API 函数"""
    fn = K[name]
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


def _ntapi(name):
    """获取 ntdll API 函数（需单独设置 restype / argtypes）"""
    return N[name]


def _uapi(name, restype, argtypes):
    """绑定 user32 API 函数"""
    fn = U[name]
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


# ============================================================
#  Win32 API 绑定
# ============================================================

_CreateNamedPipeW = _api("CreateNamedPipeW", W.HANDLE,
    [W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, ctypes.c_void_p])
_CreateFileW = _api("CreateFileW", W.HANDLE,
    [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE])
_ConnectNamedPipe = _api("ConnectNamedPipe", W.BOOL, [W.HANDLE, ctypes.c_void_p])
_CreatePseudoConsole = _api("CreatePseudoConsole", ctypes.c_long,
    [_COORD, W.HANDLE, W.HANDLE, W.DWORD, ctypes.POINTER(_HPCON)])
_ClosePseudoConsole = _api("ClosePseudoConsole", None, [_HPCON])
_ResizePseudoConsole = _api("ResizePseudoConsole", ctypes.c_long, [_HPCON, _COORD])
_ReadFile = _api("ReadFile", W.BOOL,
    [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p])
_WriteFile = _api("WriteFile", W.BOOL,
    [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p])
_GetOverlappedResult = _api("GetOverlappedResult", W.BOOL,
    [W.HANDLE, ctypes.c_void_p, ctypes.POINTER(W.DWORD), W.BOOL])
_WaitMultiple = _api("WaitForMultipleObjects", W.DWORD,
    [W.DWORD, ctypes.POINTER(W.HANDLE), W.BOOL, W.DWORD])
_CloseHandle = _api("CloseHandle", W.BOOL, [W.HANDLE])
_SetThreadErrorMode = _api("SetThreadErrorMode", W.BOOL,
    [W.DWORD, ctypes.POINTER(W.DWORD)])
_PeekNamedPipe = _api("PeekNamedPipe", W.BOOL,
    [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD),
     ctypes.POINTER(W.DWORD), ctypes.POINTER(W.DWORD)])
_CreateEventW = _api("CreateEventW", W.HANDLE,
    [ctypes.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR])
_ResetEvent = _api("ResetEvent", W.BOOL, [W.HANDLE])
_InitAttrList = _api("InitializeProcThreadAttributeList", W.BOOL,
    [ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.POINTER(ctypes.c_size_t)])
_UpdateAttr = _api("UpdateProcThreadAttribute", W.BOOL,
    [ctypes.c_void_p, W.DWORD, ctypes.c_size_t,
     ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p])
_DeleteAttrList = _api("DeleteProcThreadAttributeList", W.BOOL, [ctypes.c_void_p])
_CancelIoEx = _api("CancelIoEx", W.BOOL, [W.HANDLE, ctypes.c_void_p])
_CreateProcess = _api("CreateProcessW", W.BOOL,
    [W.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, W.BOOL, W.DWORD,
     ctypes.c_void_p, W.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p])
_CreateProcessAsUserW = _api("CreateProcessAsUserW", W.BOOL,
    [W.HANDLE, W.LPCWSTR, W.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, W.BOOL, W.DWORD,
     ctypes.c_void_p, W.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p])
_GetExitCodeProcess = _api("GetExitCodeProcess", W.BOOL,
    [W.HANDLE, ctypes.POINTER(W.DWORD)])

# ---- Job Object ----
_CreateJobObjectW = _api("CreateJobObjectW", W.HANDLE,
    [ctypes.c_void_p, W.LPCWSTR])
_AssignProcessToJobObject = _api("AssignProcessToJobObject", W.BOOL,
    [W.HANDLE, W.HANDLE])
_SetInformationJobObject = _api("SetInformationJobObject", W.BOOL,
    [W.HANDLE, W.DWORD, ctypes.c_void_p, W.DWORD])
_QueryInformationJobObject = _api("QueryInformationJobObject", W.BOOL,
    [W.HANDLE, W.DWORD, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)])

# Job Object 信息类常量
_JobObjectBasicLimitInformation = 2
_JobObjectBasicProcessIdList = 3

# JOB_OBJECT_LIMIT
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x400
_JobObjectExtendedLimitInformation = 9

# ── Job Object 完成端口通知 ──
_JobObjectAssociateCompletionPortInformation = 7

# Job 通知消息类型（Windows 10/11 SDK winnt.h 定义）
_JOB_OBJECT_MSG_NEW_PROCESS           = 6   # 新进程创建
_JOB_OBJECT_MSG_EXIT_PROCESS          = 7   # 进程退出
_JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS = 8   # 进程异常退出（崩溃）

# JOBOBJECT_ASSOCIATE_COMPLETION_PORT — 关联 Job 与 IOCP 的结构体
JOBOBJECT_ASSOCIATE_COMPLETION_PORT = type(
    "_JOBOBJECT_ASSOCIATE_COMPLETION_PORT",
    (ctypes.Structure,),
    {"_fields_": [
        ("CompletionKey", ctypes.c_void_p),
        ("CompletionPort", W.HANDLE),
    ]},
)

# ── IOCP API ──
_CreateIoCompletionPort = _api("CreateIoCompletionPort", W.HANDLE,
    [W.HANDLE, W.HANDLE, ctypes.c_void_p, W.DWORD])
_GetQueuedCompletionStatus = _api("GetQueuedCompletionStatus", W.BOOL,
    [W.HANDLE, ctypes.POINTER(W.DWORD), ctypes.POINTER(ctypes.c_void_p),
     ctypes.POINTER(ctypes.c_void_p), W.DWORD])
_PostQueuedCompletionStatus = _api("PostQueuedCompletionStatus", W.BOOL,
    [W.HANDLE, W.DWORD, ctypes.c_void_p, ctypes.c_void_p])

# IO_COUNTERS（JOBOBJECT_EXTENDED_LIMIT_INFORMATION 内嵌结构体）
_IO_COUNTERS = type(
    "_IO_COUNTERS",
    (ctypes.Structure,),
    {"_fields_": [
        ("ReadOperationCount",   ctypes.c_ulonglong),
        ("WriteOperationCount",  ctypes.c_ulonglong),
        ("OtherOperationCount",  ctypes.c_ulonglong),
        ("ReadTransferCount",    ctypes.c_ulonglong),
        ("WriteTransferCount",   ctypes.c_ulonglong),
        ("OtherTransferCount",   ctypes.c_ulonglong),
    ]},
)

JOBOBJECT_BASIC_LIMIT_INFORMATION = type(
    "_JOBOBJECT_BASIC_LIMIT_INFORMATION",
    (ctypes.Structure,),
    {"_fields_": [
        ("PerProcessUserTimeLimit",  ctypes.c_longlong),
        ("PerJobUserTimeLimit",      ctypes.c_longlong),
        ("LimitFlags",               W.DWORD),
        ("MinimumWorkingSetSize",    ctypes.c_size_t),
        ("MaximumWorkingSetSize",    ctypes.c_size_t),
        ("ActiveProcessLimit",       W.DWORD),
        ("Affinity",                 ctypes.c_size_t),
        ("PriorityClass",            W.DWORD),
        ("SchedulingClass",          W.DWORD),
    ]},
)

# JOBOBJECT_EXTENDED_LIMIT_INFORMATION — 使用此类（class 9）设置 KILL_ON_JOB_CLOSE
JOBOBJECT_EXTENDED_LIMIT_INFORMATION = type(
    "_JOBOBJECT_EXTENDED_LIMIT_INFORMATION",
    (ctypes.Structure,),
    {"_fields_": [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo",                _IO_COUNTERS),
        ("ProcessMemoryLimit",    ctypes.c_size_t),
        ("JobMemoryLimit",        ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed",     ctypes.c_size_t),
    ]},
)

_MAX_JOB_PIDS = 4096
JOBOBJECT_BASIC_PROCESS_ID_LIST = type(
    "_JOBOBJECT_BASIC_PROCESS_ID_LIST",
    (ctypes.Structure,),
    {"_fields_": [
        ("NumberOfAssignedProcesses", W.DWORD),
        ("NumberOfProcessIdsInList",  W.DWORD),
        ("ProcessIdList",             W.DWORD * _MAX_JOB_PIDS),
    ]},
)

# ---- user32 API ----
# EnumWindows 回调类型（必须保持引用防止 GC）
WNDENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HANDLE, W.LPARAM)

_EnumWindows = _uapi("EnumWindows", W.BOOL, [WNDENUMPROC, W.LPARAM])
_GetWindowThreadProcessId = _uapi("GetWindowThreadProcessId", W.DWORD,
    [W.HANDLE, ctypes.POINTER(W.DWORD)])
_GetWindowTextW = _uapi("GetWindowTextW", ctypes.c_int,
    [W.HANDLE, ctypes.c_wchar_p, ctypes.c_int])
_GetClassNameW = _uapi("GetClassNameW", ctypes.c_int,
    [W.HANDLE, ctypes.c_wchar_p, ctypes.c_int])
_IsWindowVisible = _uapi("IsWindowVisible", W.BOOL, [W.HANDLE])
_SendMessageW = _uapi("SendMessageW", ctypes.c_size_t,
    [W.HANDLE, W.UINT, ctypes.c_size_t, ctypes.c_size_t])

WM_CLOSE = 0x0010

# ---- NT API ----
_NtOpenFile = _ntapi("NtOpenFile")
_NtOpenFile.restype = W.LONG
_NtOpenFile.argtypes = [
    ctypes.POINTER(W.HANDLE), W.ULONG,
    ctypes.POINTER(_OBJECT_ATTRIBUTES),
    ctypes.POINTER(_IO_STATUS_BLOCK), W.ULONG, W.ULONG,
]

_NtSetSystemInformation = _ntapi("NtSetSystemInformation")
_NtSetSystemInformation.restype = W.LONG
_NtSetSystemInformation.argtypes = [W.INT, ctypes.c_void_p, W.ULONG]

# ── DuplicateHandle ──
_DuplicateHandle = _api("DuplicateHandle", W.BOOL,
    [W.HANDLE, W.HANDLE, W.HANDLE, ctypes.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD])

# ── 控制台输入注入（鼠标事件）──
# ConPTY 输入管道不会把 SGR 鼠标序列转换为子进程的 MOUSE_EVENT_RECORD，
# 需要附加到子进程控制台并直接写入输入记录。
_AttachConsole = _api("AttachConsole", W.BOOL, [W.DWORD])
_FreeConsole = _api("FreeConsole", W.BOOL, [])
_GetConsoleMode = _api("GetConsoleMode", W.BOOL, [W.HANDLE, ctypes.POINTER(W.DWORD)])
_SetConsoleMode = _api("SetConsoleMode", W.BOOL, [W.HANDLE, W.DWORD])
_GetConsoleOutputCP = _api("GetConsoleOutputCP", W.UINT, [])
_SetConsoleOutputCP = _api("SetConsoleOutputCP", W.BOOL, [W.UINT])
_GetConsoleCP = _api("GetConsoleCP", W.UINT, [])
_SetConsoleCP = _api("SetConsoleCP", W.BOOL, [W.UINT])
_WriteConsoleInputW = _api("WriteConsoleInputW", W.BOOL,
    [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)])
_MapVirtualKeyW = _uapi("MapVirtualKeyW", W.UINT, [W.UINT, W.UINT])

STD_INPUT_HANDLE = W.DWORD(-10).value

ENABLE_MOUSE_INPUT = 0x0010
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004
ENABLE_PROCESSED_INPUT = 0x0001

KEY_EVENT = 0x0001
MOUSE_EVENT = 0x0002
WINDOW_BUFFER_SIZE_EVENT = 0x0004
MENU_EVENT = 0x0008
FOCUS_EVENT = 0x0010

# MOUSE_EVENT_RECORD dwEventFlags
MOUSE_MOVED = 0x0001
DOUBLE_CLICK = 0x0002
MOUSE_WHEELED = 0x0004
MOUSE_HWHEELED = 0x0008

# dwButtonState button flags
FROM_LEFT_1ST_BUTTON_PRESSED = 0x0001  # left
RIGHTMOST_BUTTON_PRESSED = 0x0002       # right
FROM_LEFT_2ND_BUTTON_PRESSED = 0x0004   # middle
FROM_LEFT_3RD_BUTTON_PRESSED = 0x0008
FROM_LEFT_4TH_BUTTON_PRESSED = 0x0010

WHEEL_DELTA = 120

# dwControlKeyState modifier flags
SHIFT_PRESSED = 0x0010
LEFT_ALT_PRESSED = 0x0002
RIGHT_ALT_PRESSED = 0x0001
LEFT_CTRL_PRESSED = 0x0008
RIGHT_CTRL_PRESSED = 0x0004

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


# ============================================================
#  ConDrv 驱动可用性检测
# ============================================================

# ConDrv 直连方案已验证可行（test_condrv_manual.py）：
#   1. NtOpenFile("\\Device\\ConDrv\\Server") → CreateServerHandle
#   2. NtOpenFile("\\Reference") → CreateClientHandle
#   3. CreatePipe 信号管道 + I/O 管道
#   4. conhost.exe --headless + HANDLE_LIST + STARTF_USESTDHANDLES
#   5. HPCON {hSignal, hPtyReference, hConPtyProcess}
#   6. 子进程 PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE 附着
#
# 关键：conhost 的 STARTUPINFO 必须设置 STARTF_USESTDHANDLES + hStdInput/hStdOutput，
# 否则 conhost 不会通过管道收发 VT 数据。
# 子进程的 bInheritHandles 必须为 False（与 CreatePseudoConsole API 路径一致）。
#
# 优势：可指定任意 conhost 路径（如 OpenConsole），而 CreatePseudoConsole API
# 只能使用系统 conhost。
#
_CONDRV_OK: bool = True

# 保留 _ensure_condrv 函数定义供后续调试/恢复使用
def _ensure_condrv() -> bool:
    """检查 ConDrv 驱动是否可用，若不可用则尝试加载

    Returns:
        True 表示 ConDrv 驱动可用。
    """
    for attempt in range(2):
        h = W.HANDLE()
        name = "\\Device\\ConDrv\\Server"
        buf = ctypes.create_unicode_buffer(name)
        us = _UNICODE_STRING(
            len(name) * 2,
            (len(name) + 1) * 2,
            ctypes.cast(buf, W.LPWSTR),
        )
        oa = _OBJECT_ATTRIBUTES(
            ctypes.sizeof(_OBJECT_ATTRIBUTES),
            None, ctypes.pointer(us), 0x42, None, None,
        )
        iosb = _IO_STATUS_BLOCK()
        hr = _NtOpenFile(
            ctypes.byref(h), 0x10000000,
            ctypes.byref(oa), ctypes.byref(iosb), 7, 0,
        )
        if hr == 0:
            _CloseHandle(h)
            return True
        if attempt == 0:
            # 加载驱动后重试
            info = W.ULONG(1)
            _NtSetSystemInformation(132, ctypes.byref(info), ctypes.sizeof(W.ULONG))
    return False
