"""进程信息查询与错误消息格式化

提供按 PID 查询进程可执行文件名/路径的工具函数，
进程详情查询（命令行、父PID、内存、CPU时间、创建时间等），
进程树构建，以及进程退出码和 PTY 创建失败的错误消息格式化。
进程存在性探测（pid_exists）见 src/common/process.py（跨侧共享）。

进程字段统一由 psutil 提供（跨平台，消除手写 ctypes/PEB 偏移与 /proc 解析），
psutil 为核心必装依赖（见 requirements.txt）。
"""

import signal as _signal
from typing import Dict, List, Optional

import psutil

from ..config.common import IS_WINDOWS
from ..logging import get_logger

_logger = get_logger("pty-session")


# ── 进程信息查询 ──


def _get_process_name(pid: int) -> str:
    """根据 PID 获取进程可执行文件名称（不含路径）

    Args:
        pid: 进程 ID。

    Returns:
        可执行文件名（如 g++.exe）。获取失败时返回 'PID {pid}'。
    """
    try:
        name = psutil.Process(pid).name()
        if name:
            _logger.debug("get_process_name: pid=%d name=%s", pid, name)
            return name
    except (psutil.Error, ValueError):
        pass
    _logger.debug("get_process_name: pid=%d not found", pid)
    return f"PID {pid}"


def _get_process_path(pid: int) -> str:
    """根据 PID 获取进程可执行文件的完整路径

    Args:
        pid: 进程 ID。

    Returns:
        完整路径（如 C:\\Python311\\python.exe）。
        获取失败时返回 'PID {pid}'。
    """
    try:
        path = psutil.Process(pid).exe()
        if path:
            _logger.debug("get_process_path: pid=%d path=%s", pid, path)
            return path
    except (psutil.Error, ValueError):
        pass
    _logger.debug("get_process_path: pid=%d not found", pid)
    return f"PID {pid}"


# ── 错误消息格式化 ──

# 信号编号 → 规范名称（如 9→SIGKILL）常量表，供 _signal_name 查询，避免每次遍历
_SIGNAL_NAME_MAP = {
    getattr(_signal, name): name
    for name in dir(_signal)
    if name.startswith("SIG") and not name.startswith("SIG_")
}


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
    """获取 Unix 信号名称

    优先返回规范名（如 9→SIGKILL），无对应常量的信号回退到
    signal.strsignal 描述，仍不可得时标记为 SIGUNKNOWN。
    """
    name = _SIGNAL_NAME_MAP.get(signum)
    if name:
        return name
    try:
        desc = _signal.strsignal(signum)
        if desc:
            return desc
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

# psutil 进程详情的安全读取辅助：字段级 try/except，进程中途退出也不致整体失败。
_PROCESS_SNAPSHOT_FIELDS = ["name", "ppid"]

_SENTINEL_PPID = 0


def _get_process_snapshot() -> Optional[dict]:
    """跨平台：psutil.process_iter 一次枚举 {pid: {"name","ppid"}} 表

    供批量进程详情查询复用（同一批进程只做一次全量扫描）。
    获取失败返回 None。
    """
    try:
        table: Dict[int, Dict[str, object]] = {}
        for p in psutil.process_iter(_PROCESS_SNAPSHOT_FIELDS):
            name = p.info.get("name") or ""
            ppid = p.info.get("ppid") or _SENTINEL_PPID
            table[p.pid] = {"name": name, "ppid": ppid}
        return table
    except Exception as e:
        _logger.debug("get_process_snapshot: exception %s", e)
        return None


def _read_process_detail(pid: int) -> Optional[dict]:
    """用 psutil 读取进程详情各字段（字段级容错）

    进程可能在读取过程中退出，各字段独立容错（失败置默认值）；
    仅当进程本身无法探测（进程已消亡）时整体返回 None。
    """
    try:
        p_obj = psutil.Process(pid)
        detail = {
            "pid": pid,
            "name": "",
            "path": "",
            "commandLine": "",
            "ppid": _SENTINEL_PPID,
            "memoryMb": None,
            "cpuSeconds": None,
            "createTime": None,
        }
        try:
            detail["ppid"] = p_obj.ppid() or _SENTINEL_PPID
        except psutil.Error:
            pass
        try:
            name = p_obj.name()
            if name:
                detail["name"] = name
        except psutil.Error:
            pass
        try:
            path = p_obj.exe()
            if path:
                detail["path"] = path
        except psutil.Error:
            pass
        try:
            cmdline = p_obj.cmdline()
            if cmdline:
                detail["commandLine"] = " ".join(cmdline)
        except psutil.Error:
            pass
        try:
            mem = p_obj.memory_info()
            if mem and mem.rss:
                detail["memoryMb"] = round(mem.rss / (1024 * 1024), 1)
        except psutil.Error:
            pass
        try:
            cpu = p_obj.cpu_times()
            if cpu and (cpu.user or cpu.system):
                detail["cpuSeconds"] = round(cpu.user + cpu.system, 2)
        except psutil.Error:
            pass
        try:
            ctime = p_obj.create_time()
            if ctime:
                detail["createTime"] = float(ctime)
        except psutil.Error:
            pass
        return detail
    except (psutil.Error, ValueError):
        return None


def _get_process_detail(pid: int, snapshot: Optional[dict] = None) -> Optional[dict]:
    """根据 PID 获取进程详细信息

    Args:
        pid:      进程 ID。
        snapshot: 批量场景复用的进程快照表（{pid: {"name","ppid"}}），
                  进程在枚举后已退出时用于兜底 name/ppid。

    Returns:
        进程详情字典，包含 pid/name/path/commandLine/ppid/memoryMb/cpuSeconds/createTime。
        获取失败时返回 None。
    """
    if pid <= 0:
        return None

    detail = _read_process_detail(pid)
    if detail is not None:
        return detail

    # 进程在枚举后退出：用快照兜底 name/ppid（无内存/CPU 等再查询能力）
    if snapshot and pid in snapshot:
        _logger.debug("get_process_detail: pid=%d fallback to snapshot", pid)
        info = snapshot[pid]
        return {
            "pid": pid,
            "name": info.get("name", ""),
            "path": "",
            "commandLine": "",
            "ppid": info.get("ppid", 0),
            "memoryMb": None,
            "cpuSeconds": None,
            "createTime": None,
        }
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

    # 批量场景复用同一张进程快照，避免逐 pid 重复全量枚举
    snapshot = _get_process_snapshot()

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