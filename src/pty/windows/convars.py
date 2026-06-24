"""Windows ConPTY 常量 / ctypes 类型 / API 函数绑定

集中管理所有 Windows API 声明，作为唯一的 API 声明文件。
仅在 Windows 平台被导入。
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
    [ctypes.c_void_p, W.DWORD, ctypes.c_void_p,
     ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p])
_DeleteAttrList = _api("DeleteProcThreadAttributeList", W.BOOL, [ctypes.c_void_p])
_CancelIoEx = _api("CancelIoEx", W.BOOL, [W.HANDLE, ctypes.c_void_p])
_CreateProcess = _api("CreateProcessW", W.BOOL,
    [W.LPCWSTR, W.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, W.BOOL, W.DWORD,
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


# ============================================================
#  ConDrv 驱动可用性检测
# ============================================================

# ── 禁用 ConDrv 后端 ──
#
# 原因：直连 ConDrv 驱动（NtOpenFile("\\Device\\ConDrv\\Server")）可
# 成功启动 conhost.exe --headless，但 I/O 管道无法正常收发数据：
#   1. 双独立 CreatePipe 管道已对齐 Windows Terminal 架构
#   2. conhost.exe 命令行为 --headless + HANDLE_LIST + 管道句柄
#   3. conhost 进程已启动、子进程已附着，但 _outR 无任何数据到达
#      （pending_events 持续堆积，最终 timeout）
#
# 怀疑根因：当前系统（Win10 22H2）上，通过 CreatePipe 创建的可继承
# 句柄无法被 conhost.exe 的 GetStdHandle 正确识别；Windows Terminal
# 使用更底层的 NtCreateNamedPipeFile / 信号量同步机制建立 I/O 通道，
# 其完整的初始化序列包括 IOCTL_CONDRV_READ_IO / WRITE_OUTPUT 等
# 多个步骤，当前简化实现缺少这些握手流程。
#
# 修复优先级较低（win-conpty 后端完全可用），暂时禁用此路径。
# 若后续需要重新开启，需参考 Windows Terminal 源码实现完整的
# IOCTL 握手序列，而非仅依赖 HANDLE_LIST + CreatePipe。
#
# ── 禁用 ConDrv 后端 ──
#
# 原因：conhost.exe --headless 模式下，VT I/O 不走 hStdInput/hStdOutput 管道，
# 而是走 ConDrv 驱动的内部 IPC 通道。该通道需要通过 IOCTL 握手序列初始化：
#   1. IOCTL_CONDRV_SET_SERVER_INFORMATION 注册 InputAvailableEvent
#   2. IOCTL_CONDRV_READ_IO / COMPLETE_IO / READ_INPUT / WRITE_OUTPUT 循环
#
# kernel32.CreatePseudoConsole 内部自动完成这些 IOCTL 握手（见 winconpty.cpp），
# 但 Python 层面无法直接调用 conhost 内部的 IOCTL 接口（这些是 conhost 进程
# 内部与 ConDrv 驱动之间的通信，不是外部 API）。
#
# Windows Terminal 能工作是因为它使用 ConptyCreatePseudoConsole（同 kernel32 API），
# 而非手动 ConDrv 直连。Terminal 的 winconpty.cpp _CreatePseudoConsole 本质上
# 也是调用 kernel32.CreatePseudoConsole 的内部实现。
#
# 当前 ConPTY（kernel32_api.py）完全可用，ConDrv 直连方案不可行。
#
_CONDRV_OK: bool = False

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
