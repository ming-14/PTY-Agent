"""进程信息查询与错误消息格式化

提供按 PID 查询进程可执行文件名/路径的工具函数，
进程详情查询（命令行、父PID、内存、CPU时间等），
进程树构建，以及进程退出码和 PTY 创建失败的错误消息格式化。
"""

import os
import logging
from typing import Dict, List, Optional

from ..config.common import IS_WINDOWS

_logger = logging.getLogger("pty-session")


def pid_exists(pid: int) -> bool:
    """检查指定 PID 的进程是否存在（跨平台）

    Windows: OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) 探测句柄；
    Unix: os.kill(pid, 0) 信号探测。
    """
    if IS_WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


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
            import ctypes
            from ctypes import wintypes as W
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            hproc = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not hproc:
                _logger.debug("get_process_path: OpenProcess(%d) failed", pid)
                return f"PID {pid}"
            try:
                buf = ctypes.create_unicode_buffer(260)
                size = W.DWORD(260)
                if k32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
                    _logger.debug("get_process_path: pid=%d path=%s", pid, buf.value)
                    return buf.value
                _logger.debug("get_process_path: QueryFullProcessImageNameW(%d) failed", pid)
                return f"PID {pid}"
            finally:
                k32.CloseHandle(hproc)
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


def _get_process_detail(pid: int) -> Optional[dict]:
    """根据 PID 获取进程详细信息

    Args:
        pid: 进程 ID。

    Returns:
        进程详情字典，包含 pid/name/path/commandLine/ppid/memoryMb/cpuSeconds/createTime。
        获取失败时返回 None。
    """
    if pid <= 0:
        return None
    if IS_WINDOWS:
        return _get_process_detail_windows(pid)
    else:
        return _get_process_detail_unix(pid)


def _get_process_detail_windows(pid: int) -> Optional[dict]:
    """Windows: 通过 CreateToolhelp32Snapshot 获取进程详情"""
    try:
        import ctypes
        from ctypes import wintypes as W

        TH32CS_SNAPPROCESS = 0x00000002
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class PROCESSENTRY32W(ctypes.Structure):
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

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return None
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not k32.Process32FirstW(snap, ctypes.byref(entry)):
                return None
            while True:
                if entry.th32ProcessID == pid:
                    name = entry.szExeFile
                    ppid = entry.th32ParentProcessID
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
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
        finally:
            k32.CloseHandle(snap)
        return None
    except Exception as e:
        _logger.debug("get_process_detail_windows: pid=%d exception %s", pid, e)
        return None


def _get_process_command_line_windows(pid: int) -> Optional[str]:
    """Windows: 通过 WMI 或进程读取命令行参数"""
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        PROCESS_VM_READ = 0x0010
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        hproc = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
        if not hproc:
            return None
        try:
            try:
                import subprocess
                result = subprocess.run(
                    ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"],
                    capture_output=True, text=True, timeout=3,
                    creationflags=0x08000000,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().splitlines():
                        line = line.strip()
                        if line.startswith("CommandLine="):
                            cmd = line[len("CommandLine="):]
                            return cmd if cmd else None
            except Exception:
                pass
            return None
        finally:
            k32.CloseHandle(hproc)
    except Exception:
        return None


def _get_process_memory_windows(pid: int) -> Optional[float]:
    """Windows: 获取进程内存使用（MB）"""
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        hproc = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not hproc:
            return None
        try:
            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
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
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            if psapi.GetProcessMemoryInfo(hproc, ctypes.byref(pmc), pmc.cb):
                return round(pmc.WorkingSetSize / (1024 * 1024), 1)
            return None
        finally:
            k32.CloseHandle(hproc)
    except Exception:
        return None


def _get_process_cpu_time_windows(pid: int) -> Optional[float]:
    """Windows: 获取进程 CPU 时间（秒）"""
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        hproc = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not hproc:
            return None
        try:
            creation = ctypes.c_ulonglong()
            exit_time = ctypes.c_ulonglong()
            kernel = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            if k32.GetProcessTimes(hproc, ctypes.byref(creation), ctypes.byref(exit_time),
                                   ctypes.byref(kernel), ctypes.byref(user)):
                total_100ns = kernel.value + user.value
                return round(total_100ns / 10_000_000, 2)
            return None
        finally:
            k32.CloseHandle(hproc)
    except Exception:
        return None


def _get_process_create_time_windows(pid: int) -> Optional[float]:
    """Windows: 获取进程创建时间（Unix 时间戳）"""
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        hproc = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not hproc:
            return None
        try:
            creation = ctypes.c_ulonglong()
            exit_time = ctypes.c_ulonglong()
            kernel = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            if k32.GetProcessTimes(hproc, ctypes.byref(creation), ctypes.byref(exit_time),
                                   ctypes.byref(kernel), ctypes.byref(user)):
                import datetime
                epoch = datetime.datetime(1601, 1, 1)
                delta = datetime.timedelta(microseconds=creation.value // 10)
                dt = epoch + delta
                return dt.timestamp()
            return None
        finally:
            k32.CloseHandle(hproc)
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
                command_line = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
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
                            with open(f"/proc/uptime", "r") as uf:
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

    details: Dict[int, dict] = {}
    for pid in valid_pids:
        d = _get_process_detail(pid)
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
                ad = _get_process_detail(ancestor)
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

    def _build_node(pid: int) -> dict:
        d = details[pid]
        children = []
        for child_pid in sorted(children_map.get(pid, [])):
            children.append(_build_node(child_pid))
        return {
            "pid": d["pid"],
            "name": d["name"],
            "ppid": d.get("ppid", 0),
            "children": children,
        }

    tree = [_build_node(pid) for pid in root_pids]
    return tree, details
