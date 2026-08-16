"""进程信息查询与错误消息格式化

提供按 PID 查询进程可执行文件名/路径的工具函数，
进程详情查询（命令行、父PID、内存、CPU时间等），
进程树构建，以及进程退出码和 PTY 创建失败的错误消息格式化。
进程存在性探测（pid_exists）见 src/common/process.py（跨侧共享）。
"""

import os
from typing import Dict, List, Optional

from ..config.common import IS_WINDOWS
from ..logging import get_logger

_logger = get_logger("pty-session")

# ── Windows ctypes 绑定（模块级单次加载，避免每函数重复 WinDLL）──

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes as W

    _K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PSAPI = ctypes.WinDLL("psapi", use_last_error=True)
    _NTDLL = ctypes.WinDLL("ntdll", use_last_error=True)

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_QUERY_INFORMATION = 0x0400
    _PROCESS_VM_READ = 0x0010
    _TH32CS_SNAPPROCESS = 0x00000002

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", W.DWORD),
            ("cntUsage", W.DWORD),
            ("th32ProcessID", W.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", W.DWORD),
            ("cntThreads", W.DWORD),
            ("th32ParentProcessID", W.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", W.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    # NtQueryInformationProcess：PEB 读取命令行（替代已废弃的 wmic）
    _NtQueryInformationProcess = _NTDLL.NtQueryInformationProcess
    _NtQueryInformationProcess.restype = ctypes.c_long
    _NtQueryInformationProcess.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    _NtReadVirtualMemory = _NTDLL.NtReadVirtualMemory
    _NtReadVirtualMemory.restype = ctypes.c_long
    _NtReadVirtualMemory.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _NT_SUCCESS = 0

    # PEB 内 ProcessParameters 偏移（x64=0x20 / x86=0x10，文档化布局）
    _PEB_PROCESS_PARAMETERS_OFFSET = 0x20 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x10
    # RTL_USER_PROCESS_PARAMETERS 内 CommandLine(UNICODE_STRING) 偏移
    # x64: ...CURDIR(24B)@56 + DllPath(16B)@80 + ImagePathName(16B)@96 → 112
    # x86: ...CURDIR(12B)@36 + DllPath(8B)@48 + ImagePathName(8B)@56 → 64
    _COMMAND_LINE_OFFSET = 112 if ctypes.sizeof(ctypes.c_void_p) == 8 else 64

    class _PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("Reserved1", ctypes.c_void_p),
            ("PebBaseAddress", ctypes.c_void_p),
            ("Reserved2", ctypes.c_void_p * 2),
            ("UniqueProcessId", ctypes.c_void_p),
            ("Reserved3", ctypes.c_void_p),
        ]

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", W.USHORT),
            ("MaximumLength", W.USHORT),
            ("Buffer", ctypes.c_void_p),
        ]


# ── 进程信息查询 ──


def _get_process_name(pid: int) -> str:
    """根据 PID 获取进程可执行文件名称（不含路径）

    Args:
        pid: 进程 ID。

    Returns:
        可执行文件名（如 g++.exe）。获取失败时返回 'PID {pid}'。
    """
    full = _get_process_path(pid)
    if full.startswith("PID "):
        return full
    if IS_WINDOWS:
        name = full.rsplit("\\", 1)[-1] if "\\" in full else full
    else:
        name = full.rsplit("/", 1)[-1] if "/" in full else full
    _logger.debug("get_process_name: pid=%d name=%s", pid, name)
    return name


def _get_process_path(pid: int) -> str:
    """根据 PID 获取进程可执行文件的完整路径

    Args:
        pid: 进程 ID。

    Returns:
        完整路径（如 C:\\Python311\\python.exe）。
        获取失败时返回 'PID {pid}'。
    """
    if IS_WINDOWS:
        try:
            hproc = _K32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not hproc:
                _logger.debug("get_process_path: OpenProcess(%d) failed", pid)
                return f"PID {pid}"
            try:
                buf = ctypes.create_unicode_buffer(260)
                size = W.DWORD(260)
                if _K32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
                    _logger.debug("get_process_path: pid=%d path=%s", pid, buf.value)
                    return buf.value
                _logger.debug(
                    "get_process_path: QueryFullProcessImageNameW(%d) failed", pid
                )
                return f"PID {pid}"
            finally:
                _K32.CloseHandle(hproc)
        except Exception as e:
            _logger.debug("get_process_path: pid=%d exception %s", pid, e)
            return f"PID {pid}"
    else:
        # Unix: 尝试读取 /proc/{pid}/exe 符号链接
        try:
            path = os.readlink(f"/proc/{pid}/exe")
            _logger.debug("get_process_path: pid=%d path=%s", pid, path)
            return path
        except Exception:
            pass
        # 回退到 comm
        try:
            with open(f"/proc/{pid}/comm", "r") as f:
                name = f.read().strip()
                _logger.debug("get_process_path: pid=%d comm=%s", pid, name)
                return name
        except Exception:
            _logger.debug("get_process_path: pid=%d not found", pid)
            return f"PID {pid}"


# ── 错误消息格式化 ──


def _format_exit_code_message(exit_code: int) -> Optional[str]:
    """格式化进程退出码为可读的错误消息

    在 Windows 上尝试翻译 NTSTATUS/Win32 错误码。
    在 Unix 上对信号终止的情况提供描述。

    Args:
        exit_code: 子进程退出码。

    Returns:
        可读的错误描述字符串。退出码为 0 时返回 None。
    """
    if exit_code is None or exit_code == 0:
        return None

    if IS_WINDOWS:
        try:
            from .win32_error import format_process_exit_code

            return format_process_exit_code(exit_code)
        except ImportError:
            pass

    # Unix：信号终止（负值表示信号编号）
    if exit_code < 0:
        sig_name = _signal_name(-exit_code)
        return f"进程被信号 {sig_name} ({-exit_code}) 终止"
    # Unix：非零退出码
    return f"进程异常退出 (exit={exit_code})"


def _signal_name(signum: int) -> str:
    """获取 Unix 信号名称"""
    try:
        import signal as _sig

        for name in dir(_sig):
            if name.startswith("SIG") and not name.startswith("SIG_"):
                if getattr(_sig, name, None) == signum:
                    return name
    except Exception:
        pass
    return f"SIGUNKNOWN({signum})"


def _format_pty_error(exception: Exception) -> str:
    """格式化 PTY 创建失败的异常为可读的错误消息

    在 Windows 上尝试翻译 OSError 中的错误码。

    Args:
        exception: PTY 创建时抛出的异常。

    Returns:
        可读的错误描述字符串。
    """
    if IS_WINDOWS and isinstance(exception, OSError) and exception.args:
        try:
            # OSError 格式：(error_code, message)
            if len(exception.args) >= 2 and isinstance(exception.args[0], int):
                from .win32_error import format_create_process_error

                return format_create_process_error(exception.args[0])
        except ImportError:
            pass
    return str(exception)


# ── 进程详情查询 ──


def _get_process_detail(pid: int, snapshot: Optional[dict] = None) -> Optional[dict]:
    """根据 PID 获取进程详细信息

    Args:
        pid:      进程 ID。
        snapshot: Windows 进程快照表（{pid: {"name","ppid"}}），批量场景复用。

    Returns:
        进程详情字典，包含 pid/name/path/commandLine/ppid/memoryMb/cpuSeconds/createTime。
        获取失败时返回 None。
    """
    if pid <= 0:
        return None
    if IS_WINDOWS:
        return _get_process_detail_windows(pid, snapshot)
    else:
        return _get_process_detail_unix(pid)


def _get_process_snapshot_windows() -> Optional[dict]:
    """Windows: 一次 CreateToolhelp32Snapshot 建立 {pid: {"name","ppid"}} 表

    供批量进程详情查询复用（同一批进程只做一次全量快照扫描，
    避免逐 pid 重复 CreateToolhelp32Snapshot 线性扫描）。
    获取失败返回 None。
    """
    try:
        snap = _K32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return None
        try:
            table = {}
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            if not _K32.Process32FirstW(snap, ctypes.byref(entry)):
                return None
            while True:
                table[entry.th32ProcessID] = {
                    "name": entry.szExeFile,
                    "ppid": entry.th32ParentProcessID,
                }
                entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
                if not _K32.Process32NextW(snap, ctypes.byref(entry)):
                    break
            return table
        finally:
            _K32.CloseHandle(snap)
    except Exception as e:
        _logger.debug("get_process_snapshot_windows: exception %s", e)
        return None


def _get_process_detail_windows(pid: int,
                                snapshot: Optional[dict] = None) -> Optional[dict]:
    """Windows: 通过 CreateToolhelp32Snapshot 获取进程详情

    Args:
        pid:      进程 ID。
        snapshot: 调用方已建立的进程快照表（{pid: {"name","ppid"}}，
                  批量场景复用避免重复全量扫描）；None 时自行建立。
    """
    try:
        if snapshot is None:
            snapshot = _get_process_snapshot_windows()
        if snapshot is None:
            return None
        info = snapshot.get(pid)
        if info is None:
            return None
        name = info["name"]
        ppid = info["ppid"]
        path = _get_process_path(pid)
        if path.startswith("PID "):
            path = name
        command_line = _get_process_command_line_windows(pid)
        memory_mb = _get_process_memory_windows(pid)
        cpu_seconds = _get_process_cpu_time_windows(pid)
        create_time = _get_process_create_time_windows(pid)
        return {
            "pid": pid,
            "name": name,
            "path": path if path and not path.startswith("PID ") else "",
            "commandLine": command_line or "",
            "ppid": ppid,
            "memoryMb": memory_mb,
            "cpuSeconds": cpu_seconds,
            "createTime": create_time,
        }
    except Exception as e:
        _logger.debug("get_process_detail_windows: pid=%d exception %s", pid, e)
        return None


def _get_process_command_line_windows(pid: int) -> Optional[str]:
    """Windows: 通过 NtQueryInformationProcess 读取 PEB 命令行

    替代已废弃且每次启动开销数百 ms 的 wmic 子进程：纯内存读取，
    仅需 PROCESS_QUERY_INFORMATION | PROCESS_VM_READ 权限（同用户进程通常可读）；
    权限不足（如系统进程）或跨 WoW64 架构时返回 None。
    """
    try:
        hproc = _K32.OpenProcess(
            _PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid
        )
        if not hproc:
            return None
        try:
            # 1. 取 PEB 基址
            pbi = _PROCESS_BASIC_INFORMATION()
            ret = ctypes.c_ulong(0)
            status = _NtQueryInformationProcess(
                hproc,
                0,  # ProcessBasicInformation
                ctypes.byref(pbi),
                ctypes.sizeof(pbi),
                ctypes.byref(ret),
            )
            if status != _NT_SUCCESS or not pbi.PebBaseAddress:
                return None
            # 2. 读 PEB.ProcessParameters 指针
            pp_ptr = ctypes.c_void_p(0)
            read = ctypes.c_size_t(0)
            status = _NtReadVirtualMemory(
                hproc,
                ctypes.c_void_p(
                    pbi.PebBaseAddress + _PEB_PROCESS_PARAMETERS_OFFSET
                ),
                ctypes.byref(pp_ptr),
                ctypes.sizeof(pp_ptr),
                ctypes.byref(read),
            )
            if status != _NT_SUCCESS or not pp_ptr.value:
                return None
            # 3. 读 RTL_USER_PROCESS_PARAMETERS 头（含 CommandLine UNICODE_STRING）
            head = ctypes.create_string_buffer(_COMMAND_LINE_OFFSET + 16)
            read = ctypes.c_size_t(0)
            status = _NtReadVirtualMemory(
                hproc,
                ctypes.c_void_p(pp_ptr.value),
                head,
                len(head),
                ctypes.byref(read),
            )
            if status != _NT_SUCCESS:
                return None
            cmd = _UNICODE_STRING.from_buffer_copy(head, _COMMAND_LINE_OFFSET)
            length = cmd.Length
            if not length or not cmd.Buffer:
                return None
            # 4. 读命令行字节（UTF-16LE）
            raw = ctypes.create_string_buffer(length)
            read = ctypes.c_size_t(0)
            status = _NtReadVirtualMemory(
                hproc,
                ctypes.c_void_p(cmd.Buffer),
                raw,
                length,
                ctypes.byref(read),
            )
            if status != _NT_SUCCESS:
                return None
            return raw.raw.decode("utf-16-le", errors="replace") or None
        finally:
            _K32.CloseHandle(hproc)
    except Exception:
        return None


def _get_process_memory_windows(pid: int) -> Optional[float]:
    """Windows: 获取进程内存使用（MB）"""
    try:
        hproc = _K32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not hproc:
            return None
        try:
            pmc = _PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
            if _PSAPI.GetProcessMemoryInfo(hproc, ctypes.byref(pmc), pmc.cb):
                return round(pmc.WorkingSetSize / (1024 * 1024), 1)
            return None
        finally:
            _K32.CloseHandle(hproc)
    except Exception:
        return None


def _get_process_cpu_time_windows(pid: int) -> Optional[float]:
    """Windows: 获取进程 CPU 时间（秒）"""
    try:
        hproc = _K32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not hproc:
            return None
        try:
            creation = ctypes.c_ulonglong()
            exit_time = ctypes.c_ulonglong()
            kernel = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            if _K32.GetProcessTimes(
                hproc,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                total_100ns = kernel.value + user.value
                return round(total_100ns / 10_000_000, 2)
            return None
        finally:
            _K32.CloseHandle(hproc)
    except Exception:
        return None


def _get_process_create_time_windows(pid: int) -> Optional[float]:
    """Windows: 获取进程创建时间（Unix 时间戳）"""
    try:
        hproc = _K32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not hproc:
            return None
        try:
            creation = ctypes.c_ulonglong()
            exit_time = ctypes.c_ulonglong()
            kernel = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            if _K32.GetProcessTimes(
                hproc,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                import datetime

                epoch = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
                delta = datetime.timedelta(microseconds=creation.value // 10)
                dt = epoch + delta
                return dt.timestamp()
            return None
        finally:
            _K32.CloseHandle(hproc)
    except Exception:
        return None


def _get_process_detail_unix(pid: int) -> Optional[dict]:
    """Unix: 通过 /proc/{pid}/ 获取进程详情"""
    try:
        proc_dir = f"/proc/{pid}"
        if not os.path.isdir(proc_dir):
            return None

        name = ""
        path = ""
        command_line = ""
        ppid = 0
        memory_mb = None
        cpu_seconds = None
        create_time = None

        try:
            with open(f"{proc_dir}/comm", "r") as f:
                name = f.read().strip()
        except Exception:
            name = _get_process_name(pid)
            if name.startswith("PID "):
                name = ""

        path = _get_process_path(pid)
        if path.startswith("PID "):
            path = ""

        try:
            with open(f"{proc_dir}/cmdline", "rb") as f:
                raw = f.read()
                command_line = (
                    raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
                )
        except Exception:
            pass

        try:
            with open(f"{proc_dir}/stat", "r") as f:
                stat = f.read()
                parts = stat.split(")")
                if len(parts) >= 2:
                    rest = parts[1].split()
                    if len(rest) >= 2:
                        ppid = int(rest[1])
                    if len(rest) >= 13:
                        utime = int(rest[11])
                        stime = int(rest[12])
                        try:
                            with open("/proc/uptime", "r") as uf:
                                clk_tck = os.sysconf("SC_CLK_TCK")
                                cpu_seconds = round((utime + stime) / clk_tck, 2)
                        except Exception:
                            pass
        except Exception:
            pass

        try:
            with open(f"{proc_dir}/statm", "r") as f:
                statm = f.read().split()
                if len(statm) >= 2:
                    page_size = os.sysconf("SC_PAGE_SIZE")
                    rss_pages = int(statm[1])
                    memory_mb = round(rss_pages * page_size / (1024 * 1024), 1)
        except Exception:
            pass

        try:
            stat_info = os.stat(f"{proc_dir}")
            create_time = stat_info.st_ctime
        except Exception:
            pass

        return {
            "pid": pid,
            "name": name,
            "path": path,
            "commandLine": command_line,
            "ppid": ppid,
            "memoryMb": memory_mb,
            "cpuSeconds": cpu_seconds,
            "createTime": create_time,
        }
    except Exception as e:
        _logger.debug("get_process_detail_unix: pid=%d exception %s", pid, e)
        return None


def _get_process_tree(pids: List[int], root_pid: int = 0) -> tuple:
    """根据 PID 列表构建进程树

    自动向上追溯根节点的父进程链，将祖先纳入树中，
    使树具有完整的层级结构。

    Args:
        pids: 进程 ID 列表（通常来自 ProcessTreeTracker.get_process_list()）。
        root_pid: 会话主进程 PID，向上追溯祖先时到此为止。

    Returns:
        (tree, details) 元组。
        tree 为树形结构列表，每项包含 pid/name/ppid/children 字段。
        details 为进程详情字典，key 为 pid (int)，value 为详情字典。
    """
    if not pids:
        return [], {}

    valid_pids = [p for p in pids if p > 0]
    if not valid_pids:
        return [], {}

    # 批量场景复用同一张进程快照（Windows），避免逐 pid 全量扫描
    snapshot = _get_process_snapshot_windows() if IS_WINDOWS else None

    details: Dict[int, dict] = {}
    for pid in valid_pids:
        d = _get_process_detail(pid, snapshot)
        if d:
            details[pid] = d
        else:
            name = _get_process_name(pid)
            details[pid] = {
                "pid": pid,
                "name": name if not name.startswith("PID ") else f"PID {pid}",
                "path": "",
                "commandLine": "",
                "ppid": 0,
                "memoryMb": None,
                "cpuSeconds": None,
                "createTime": None,
            }

    pid_set = set(valid_pids)

    effective_parent: Dict[int, int] = {}
    for pid in list(pid_set):
        ppid = details[pid].get("ppid", 0)
        if ppid and ppid in pid_set:
            effective_parent[pid] = ppid
        else:
            ancestor = ppid
            visited = {pid}
            while ancestor and ancestor > 0 and ancestor not in pid_set:
                if ancestor in visited:
                    break
                if root_pid and pid == root_pid:
                    break
                visited.add(ancestor)
                ad = _get_process_detail(ancestor, snapshot)
                if not ad:
                    break
                details[ancestor] = ad
                pid_set.add(ancestor)
                if root_pid and ancestor == root_pid:
                    break
                ancestor = ad.get("ppid", 0)

    children_map: Dict[int, List[int]] = {}
    root_pids: List[int] = []

    for pid in pid_set:
        ppid = details[pid].get("ppid", 0)
        if ppid and ppid in pid_set:
            children_map.setdefault(ppid, []).append(pid)
        else:
            root_pids.append(pid)

    # PID 复用竞态可能产生假父子环（父链 ppid 回指已追溯进程），
    # 导致无自然根；任取最小 pid 作根兜底，构建时按路径防环
    if not root_pids and pid_set:
        root_pids = [min(pid_set)]

    def _build_node(pid: int, _seen: frozenset) -> dict:
        d = details[pid]
        children = []
        for child_pid in sorted(children_map.get(pid, [])):
            if child_pid in _seen:
                continue
            children.append(_build_node(child_pid, _seen | {pid}))
        return {
            "pid": d["pid"],
            "name": d["name"],
            "ppid": d.get("ppid", 0),
            "children": children,
        }

    tree = [_build_node(pid, frozenset()) for pid in root_pids]
    return tree, details
