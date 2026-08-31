"""UnixProcessTracker — Unix 进程树追踪（/proc 文件系统）

通过 /proc 文件系统遍历构建进程树，为 Linux 后端提供与 Windows Job Object
对齐的进程树管理能力：

- get_process_list()  — 全进程树 PID 列表
- kill_tree()         — 终止整个进程树（后代优先，最后杀根）

说明：
- 退出码查询（get_exit_code）不在本类实现：由 UnixPseudoTerminal 对直接
  子进程 waitpid 一次并缓存，避免重复 waitpid 竞争导致退出码丢失。
  孙进程退出码无法获取（Unix 限制），与 Windows GetExitCodeProcess 的
  行为差异是平台固有的。
- /proc 遍历在进程数极多时可能较慢（但通常 PTY 场景下进程树很小）。
"""

import logging
import os
import signal
from typing import List, Optional, Set

_logger = logging.getLogger("pty-unix-tracker")


class UnixProcessTracker:
    """Unix 进程树追踪器

    通过 /proc 文件系统遍历进程树，无需外部依赖（如 psutil）。
    用于对齐 Windows Job Object 的进程树管理能力。

    Attributes:
        root_pid: 进程树根进程 PID。
    """

    def __init__(self, root_pid: int):
        """初始化追踪器

        Args:
            root_pid: 根进程 PID（通常是 PTY 子进程）。
        """
        self._root_pid = root_pid

    # ── 公开查询方法 ──

    def get_process_list(self) -> List[int]:
        """获取根进程及其所有后代 PID 列表

        BFS 遍历 /proc 构建进程树，返回包含根进程在内的全部 PID。

        Returns:
            PID 列表（根进程在前，后代按 BFS 顺序）。
        """
        descendants = self._find_descendants()
        return [self._root_pid] + descendants

    def get_descendants(self) -> List[int]:
        """获取所有后代 PID（不含根进程）

        Returns:
            后代 PID 列表（BFS 顺序）。
        """
        return self._find_descendants()

    # ── 进程树终止 ──

    def kill_tree(self):
        """终止整个进程树（后代优先，最后杀根）

        策略：
        1. 通过 /proc BFS 找到所有后代 PID
        2. 先 SIGKILL 后代（叶子优先，避免孤儿进程残留）
        3. 最后 SIGKILL 根进程

        与 Windows KILL_ON_JOB_CLOSE 对齐：直接强杀，不留清理机会。
        """
        descendants = self._find_descendants()
        # 后代倒序 = 叶子优先杀
        for pid in reversed(descendants):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
        # 最后杀根进程
        try:
            os.kill(self._root_pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    # ── 内部辅助 ──

    def _find_descendants(self) -> List[int]:
        """BFS 搜索根进程的所有后代 PID

        Returns:
            后代 PID 列表（BFS 顺序，按层次遍历）。
        """
        try:
            pid_map = self._build_pid_map()
        except Exception:
            return []
        # 如果根进程已不存在，pid_map 中无它，BFS 自然返回空
        descendants = []
        queue = [self._root_pid]
        visited: Set[int] = set()
        while queue:
            parent = queue.pop(0)
            if parent != self._root_pid:
                descendants.append(parent)
            for pid, ppid in pid_map.items():
                if ppid == parent and pid not in visited:
                    visited.add(pid)
                    queue.append(pid)
        return descendants

    @staticmethod
    def _build_pid_map() -> dict:
        """遍历 /proc 构建 {pid: ppid} 映射

        Returns:
            {pid: ppid} 字典。遍历过程中进程可能退出，
            因此不保证每个 pid 的 /proc/<pid>/stat 都能读取。
        """
        pid_map = {}
        try:
            for entry in os.listdir("/proc"):
                if entry.isdigit():
                    try:
                        pid = int(entry)
                        ppid = UnixProcessTracker._get_ppid(pid)
                        if ppid is not None:
                            pid_map[pid] = ppid
                    except (ValueError, OSError):
                        pass
        except FileNotFoundError:
            # /proc 不可用（非 Linux 系统）/ 容器环境
            pass
        return pid_map

    @staticmethod
    def _get_ppid(pid: int) -> Optional[int]:
        """从 /proc/<pid>/stat 读取父进程 PID

        stat 格式: pid (comm) state ppid ...
        comm 可能包含括号和空格，从最后一个 ')' 后解析。

        Args:
            pid: 目标进程 PID。

        Returns:
            ppid（int），读取失败返回 None。
        """
        try:
            with open(f"/proc/{pid}/stat", "r") as f:
                data = f.read()
            idx = data.rfind(")")
            if idx == -1:
                return None
            fields = data[idx + 1:].split()
            if len(fields) >= 2:
                return int(fields[1])  # ppid = 第 4 字段（索引 2）
            return None
        except (FileNotFoundError, ValueError, OSError, IndexError):
            return None