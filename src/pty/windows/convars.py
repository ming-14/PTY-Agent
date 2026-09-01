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
_SendMessageW = _uapi("SendMessageW", ctypes.c_size_t,
    [W.HANDLE, W.UINT, ctypes.c_size_t, ctypes.c_size_t])

WM_CLOSE = 0x0010