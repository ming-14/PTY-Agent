"""Windows ConPTY 常量 / ctypes 类型 / API 函数绑定

集中管理所有 Windows API 声明，作为唯一的 API 声明文件。
仅在 Windows 平台被导入。
"""

import ctypes
from ctypes import wintypes as W

# ── DLL 句柄 ──
K = ctypes.WinDLL("kernel32", use_last_error=True)
U = ctypes.WinDLL("user32", use_last_error=True)


# ============================================================
#  NT 类型定义
# ============================================================

class _COORD(ctypes.Structure):
    _fields_ = [
        ("X", W.SHORT),
        ("Y", W.SHORT),
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


# ============================================================
#  API 绑定辅助
# ============================================================

def _api(name, restype, argtypes):
    """绑定 kernel32 API 函数"""
    fn = K[name]
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


def _uapi(name, restype, argtypes):
    """绑定 user32 API 函数"""
    fn = U[name]
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


# ============================================================
#  Win32 API 绑定
# ============================================================

_CreatePseudoConsole = _api("CreatePseudoConsole", ctypes.c_long,
    [_COORD, W.HANDLE, W.HANDLE, W.DWORD, ctypes.POINTER(_HPCON)])
_ClosePseudoConsole = _api("ClosePseudoConsole", None, [_HPCON])
_ReadFile = _api("ReadFile", W.BOOL,
    [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p])
_WriteFile = _api("WriteFile", W.BOOL,
    [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p])
_CloseHandle = _api("CloseHandle", W.BOOL, [W.HANDLE])
_SetThreadErrorMode = _api("SetThreadErrorMode", W.BOOL,
    [W.DWORD, ctypes.POINTER(W.DWORD)])
_PeekNamedPipe = _api("PeekNamedPipe", W.BOOL,
    [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD),
     ctypes.POINTER(W.DWORD), ctypes.POINTER(W.DWORD)])
_InitAttrList = _api("InitializeProcThreadAttributeList", W.BOOL,
    [ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.POINTER(ctypes.c_size_t)])
_UpdateAttr = _api("UpdateProcThreadAttribute", W.BOOL,
    [ctypes.c_void_p, W.DWORD, ctypes.c_void_p,
     ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p])
_DeleteAttrList = _api("DeleteProcThreadAttributeList", W.BOOL, [ctypes.c_void_p])
_CreateProcess = _api("CreateProcessW", W.BOOL,
    [W.LPCWSTR, W.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, W.BOOL, W.DWORD,
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
        # ULONG_PTR = 8 字节（64位），DWORD 会导致 PID 列表错位
        ("ProcessIdList",             ctypes.c_size_t * _MAX_JOB_PIDS),
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
try:
    # 64 位首选 GetWindowLongPtrW；32 位下该导出名不存在，回退 GetWindowLongW
    _GetWindowStyle = _uapi("GetWindowLongPtrW", ctypes.c_ssize_t,
                           [W.HANDLE, ctypes.c_int])
except AttributeError:      # pragma: no cover - 32 位 Python
    _GetWindowStyle = _uapi("GetWindowLongW", ctypes.c_long,
                            [W.HANDLE, ctypes.c_int])
_SendMessageW = _uapi("SendMessageW", ctypes.c_size_t,
    [W.HANDLE, W.UINT, ctypes.c_size_t, ctypes.c_size_t])

WM_CLOSE = 0x0010
WM_GETTEXT = 0x000D        # 跨进程发送消息取标题（故需超时保护）

# ============================================================
#  WinEvent Hook —— GUI 窗口事件驱动检测（替代纯轮询）
#
#  SetWinEventHook(WINEVENT_OUTOFCONTEXT) 由系统主动推送"窗口显示"事件，
#  回调投递到装载 hook 的线程，故该线程必须持续抽取消息队列。
#  按 PID 装载（idProcess=目标 PID）：只接收被追踪进程的窗口事件，
#  不会收到全系统的窗口洪水。
# ============================================================

# WinEvent 事件号（winuser.h）
EVENT_MIN = 0x00000000
EVENT_MAX = 0xFFFFFFFF
EVENT_OBJECT_CREATE = 0x80000000
EVENT_OBJECT_DESTROY = 0x80010000
EVENT_OBJECT_SHOW = 0x80020000
EVENT_OBJECT_HIDE = 0x80030000
# 实测（Win11/Python3.11 ctypes）：object 事件的 event 参数以 `常量 >> 16`
# 的形式送达（如 SHOW 到达为 0x8002），且把 eventMin/Max 限定在
# 0x80000000+ 区间会一条都收不到。故装载一律用全范围，回调内**不依赖
# event 号**，改为直接校验 hwnd 的可见性与样式 —— 见 gui_monitor 的注释。

# WinEvent Hook 标志
WINEVENT_OUTOFCONTEXT = 0x0000   # 回调在本进程执行（唯一安全选项）
WINEVENT_SKIPOWNPROCESS = 0x0002 # 忽略本进程自己的窗口

# 回调参数过滤常量
OBJID_WINDOW = 0x00000000        # idObject == 窗口本身（非菜单/滚动条等）
CHILDID_SELF = 0                 # idChild == 元素自身（非子项）

# 顶层窗口判定：**不要**用 GetAncestor(GA_PARENT) —— 实测它对 Tk 的 TkTopLevel
# 也返回非零父，会把真窗口全判成子窗口。改用 WS_CHILD 样式位（见下）。
# 因此本模块不再导出 GetAncestor，避免这个错误判据被再次采用。

# 窗口样式（顶层窗口判定）
GWL_STYLE = -16
WS_CHILD = 0x40000000

# 消息泵相关（OUTOFCONTEXT 回调只投递给装载 hook 的线程，
# 且在该线程抽取消息队列时才被调用 —— 故需要一条专用泵线程）
WM_QUIT = 0x0012
WM_APP_INSTALL_HOOK = 0x8001   # WM_APP+1：本模块私有，通知泵线程装载新 PID 的 hook
WM_APP_REMOVE_HOOK = 0x8002    # WM_APP+2：进程退出后卸载其 hook，避免长时间会话累积

# SendMessageTimeoutW 标志：目标窗口线程挂死时不无限等待
SMTO_ABORTIFHUNG = 0x0002
SMTO_NORMAL = 0x0000

# WINEVENTPROC: void (HWINEVENTHOOK, DWORD event, HWND, LONG idObject,
#                  LONG idChild, DWORD dwEventThread, DWORD dwmsEventTime)
WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, W.HANDLE, W.UINT, W.HWND, W.LONG, W.LONG, W.DWORD, W.DWORD)


class MSG(ctypes.Structure):
    """user32 消息结构（消息泵抽取队列用）"""
    _fields_ = [
        ("hwnd", W.HWND),
        ("message", W.UINT),
        ("wParam", W.WPARAM),
        ("lParam", W.LPARAM),
        ("time", W.DWORD),
        ("pt_x", W.LONG),
        ("pt_y", W.LONG),
    ]


_SetWinEventHook = _uapi("SetWinEventHook", W.HANDLE, [
    W.UINT, W.UINT, W.HINSTANCE, WINEVENTPROC,
    W.DWORD, W.DWORD, W.UINT,
])
_UnhookWinEvent = _uapi("UnhookWinEvent", W.BOOL, [W.HANDLE])
_SendMessageTimeoutW = _uapi("SendMessageTimeoutW", ctypes.c_ssize_t, [
    W.HWND, W.UINT, ctypes.c_size_t, ctypes.c_ssize_t, W.UINT, W.UINT,
    ctypes.POINTER(ctypes.c_size_t),
])
_GetMessageW = _uapi("GetMessageW", ctypes.c_int, [
    ctypes.POINTER(MSG), W.HWND, W.UINT, W.UINT,
])
# 队列在首次 GetMessage/PeekMessage 时才创建；PostThreadMessage 打到无队列
# 线程会静默失败，故泵线程需先用 PeekMessage(PM_NOREMOVE) 把队列建起来。
_PeekMessageW = _uapi("PeekMessageW", W.BOOL, [
    ctypes.POINTER(MSG), W.HWND, W.UINT, W.UINT, W.UINT,
])
PM_NOREMOVE = 0x0000
_TranslateMessage = _uapi("TranslateMessage", W.BOOL, [ctypes.POINTER(MSG)])
_DispatchMessageW = _uapi("DispatchMessageW", ctypes.c_ssize_t,
                          [ctypes.POINTER(MSG)])

_GetCurrentThreadId = _api("GetCurrentThreadId", W.DWORD, [])
# PostThreadMessage 属于 user32（kernel32 中不存在，误绑会在导入期抛
# AttributeError: function 'PostThreadMessageW' not found）
_PostThreadMessageW = _uapi("PostThreadMessageW", W.BOOL, [
    W.DWORD, W.UINT, W.WPARAM, W.LPARAM,
])