"""PgidProcessTreeTracker — Unix 进程树追踪实现（基于 process group）

迁移自 `pty/unix/process.py` 的 UnixProcessMonitor，实现
`ProcessTreeTracker` 抽象端口（见 design/process-manager-refactor.md §3.2）。

- register_root：fork/setsid 后登记 root，捕获 pgid（子/孙进程共享）
- kill_tree：killpg SIGTERM → 超时 SIGKILL
- get_root_exit_code：唯一 waitpid 直接子进程的收尸点（消除 pty 层双收尸竞争）
- drain_notifications：进程列表 diff + waitpid 结果，映射为 ProcessNotification

仅 POSIX 平台被导入。
"""

import os
import signal
import logging
import threading
import time
from typing import Optional, List

from ..base import ProcessTreeTracker, ProcessNotification, NOTIF_SPAWN, NOTIF_EXIT, NOTIF_CRASH

_logger = logging.getLogger("process-pgid-tracker")


class PgidProcessTreeTracker(ProcessTreeTracker):
    """Unix 进程树追踪器（process group）

    子进程通过 os.setsid() 创建新会话后，同一会话内的所有子/孙进程共享
    pgid。利用 pgid 可以追踪、终止整个进程树。

    崩溃检测采用 waitpid 轮询（与 Windows IOCP 推送不同），
    由上层 monitor 定期调用 drain_notifications()。
    """

    def __init__(self):
        self._root_pid: Optional[int] = None
        self._pgid: Optional[int] = None
        self._reaped = False
        self._exit_code: Optional[int] = None
        self._last_pids: List[int] = []
        self._notifications: List[ProcessNotification] = []
        self._lock = threading.Lock()

    @property
    def pgid(self) -> Optional[int]:
        return self._pgid

    # ── 登记 ──

    def register_root(self, pid: int, hprocess: Optional[int] = None) -> bool:
        """登记 root 进程并捕获 pgid

        PTY spawn（setsid）成功后立即调用，捕获 root 的 pgid，
        此后整个进程树归本 tracker 管理。

        Returns:
            True 登记成功（pgid 捕获成功）；捕获失败回退 root pid 作 pgid。
        """
        self._root_pid = pid
        try:
            self._pgid = os.getpgid(pid)
        except OSError:
            self._pgid = pid
        _logger.info("register_root: pid=%d pgid=%d", pid, self._pgid)
        return True

    # ── 进程树查询 ──

    def get_process_list(self) -> List[int]:
        """获取同 pgid 所有进程的 PID 列表"""
        pids = self._get_pids_procfs()
        if pids is not None:
            return pids
        pids = self._get_pids_ps()
        if pids is not None:
            return pids
        if self.is_root_alive():
            return [self._root_pid]
        return []

    def is_root_alive(self) -> bool:
        """root 进程是否存活"""
        if not self._root_pid:
            return False
        try:
            os.kill(self._root_pid, 0)
            return True
        except OSError:
            return False

    # ── 终止 ──

    def kill_tree(self, timeout: float = 3.0):
        """终止整个进程树：SIGTERM → 等待 → 超时 SIGKILL"""
        if not self._pgid:
            return
        try:
            os.killpg(self._pgid, signal.SIGTERM)
            _logger.info("kill_tree: SIGTERM sent to pgid=%d", self._pgid)
        except OSError as e:
            _logger.debug("kill_tree: SIGTERM failed (process may have exited): %s", e)
            if self._root_pid:
                try:
                    os.kill(self._root_pid, signal.SIGTERM)
                except OSError:
                    pass
            return

        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._pgid_alive():
                _logger.info("kill_tree: process group exited after SIGTERM")
                return
            time.sleep(0.1)

        try:
            os.killpg(self._pgid, signal.SIGKILL)
            _logger.info("kill_tree: SIGKILL sent to pgid=%d", self._pgid)
        except OSError:
            pass

    def _pgid_alive(self) -> bool:
        """进程组内是否有存活进程"""
        try:
            os.killpg(self._pgid, 0)
            return True
        except OSError:
            return False

    # ── 退出码（唯一 waitpid 收尸点）──

    def get_root_exit_code(self) -> Optional[int]:
        """waitpid 收尸 root 并返回退出码

        本方法是 tracker 内唯一调用 os.waitpid 的地方（phase 设计 §4.2），
        pty 层不再自行收尸，从根上消除双收尸竞争。
        """
        if self._reaped:
            return self._exit_code
        if not self._root_pid:
            return None
        try:
            pid, status = os.waitpid(self._root_pid, os.WNOHANG)
            if pid == 0:
                return None
            self._reaped = True
            self._exit_code = self._extract_exit_code(status)
            return self._exit_code
        except ChildProcessError:
            self._reaped = True
            return self._exit_code
        except Exception:
            return None

    def get_process_exit_code(self, pid: int) -> Optional[int]:
        """查询指定 PID 的进程退出码（仅 root 有缓存，其余返回 None）"""
        if pid == self._root_pid:
            return self.get_root_exit_code()
        return None

    @staticmethod
    def _extract_exit_code(status: int) -> Optional[int]:
        """从 waitpid status 提取退出码（正常退出返回码 / 信号取负）"""
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        return None

    # ── 通知 ──

    def drain_notifications(self) -> List[ProcessNotification]:
        """进程列表 diff + waitpid 收尸结果 → 通知列表"""
        self._detect_process_changes()
        self._check_crash_internal()
        with self._lock:
            notifications = self._notifications[:]
            self._notifications.clear()
            return notifications

    def _detect_process_changes(self):
        """进程列表 diff 生成 spawn / exit 通知"""
        current = set(self.get_process_list())
        with self._lock:
            previous = set(self._last_pids)
        new_pids = current - previous
        gone_pids = previous - current
        with self._lock:
            for pid in new_pids:
                self._notifications.append(
                    ProcessNotification(NOTIF_SPAWN, pid=pid)
                )
            for pid in gone_pids:
                self._notifications.append(
                    ProcessNotification(NOTIF_EXIT, pid=pid)
                )
            self._last_pids = list(current)

    def _check_crash_internal(self):
        """waitpid 收尸 root，非 0 退出码 → crash 通知（收尸同时进行）"""
        if self._reaped:
            return
        exit_code = self.get_root_exit_code()
        if exit_code is None:
            return
        if exit_code != 0:
            _logger.info("root crash: pid=%d exit=%s", self._root_pid, exit_code)
            with self._lock:
                self._notifications.append(
                    ProcessNotification(NOTIF_CRASH, pid=self._root_pid, exit_code=exit_code)
                )
        else:
            with self._lock:
                self._notifications.append(
                    ProcessNotification(NOTIF_EXIT, pid=self._root_pid, exit_code=exit_code)
                )

    # ── 进程列表来源 ──

    def _get_pids_procfs(self) -> Optional[List[int]]:
        try:
            pids = []
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                stat_path = f"/proc/{entry}/stat"
                try:
                    with open(stat_path, "r") as f:
                        content = f.read()
                    # /proc/pid/stat 格式: pid (comm) state ppid pgrp session ...
                    # comm 可能含空格甚至括号，不能直接用 split(" ") 解析。
                    # 正确做法：找到最后一个 ')' 确定 comm 结尾，再解析后续字段。
                    rparen = content.rfind(")")
                    if rparen == -1:
                        continue
                    rest = content[rparen + 1:].split()
                    if len(rest) >= 3:
                        pgrp = int(rest[2])
                        if pgrp == self._pgid:
                            # entry 就是目录名=pid，无需从 stat 中重新解析
                            pids.append(int(entry))
                except (ValueError, OSError, IndexError):
                    continue
            return pids
        except Exception:
            return None

    def _get_pids_ps(self) -> Optional[List[int]]:
        try:
            import subprocess
            result = subprocess.run(
                ["ps", "-o", "pid=", "-g", str(self._pgid)],
                capture_output=True, text=True, timeout=2,
            )
            pids = []
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
            return pids if pids else None
        except Exception:
            return None

    # ── 生命周期 ──

    def close(self):
        """清理内部状态（进程组由内核回收）"""
        self._last_pids = []
        with self._lock:
            self._notifications.clear()
