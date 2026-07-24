"""Unix 进程树监控 — 基于 process group (pgid) 的进程追踪、崩溃检测与终止

子进程通过 os.setsid() 创建新会话后，同一会话内的所有子/孙进程共享 pgid。
利用 pgid 可以：
- 追踪整个进程树（遍历 /proc 或 ps 找同 pgid 进程）
- 杀死整个进程树（os.killpg）
- 检测进程树是否存活（os.killpg(pgid, 0)）

崩溃检测采用 waitpid 轮询（与 Windows IOCP 推送不同），
由 Session 的 _monitor_loop 每 2 秒调用 drain_notifications()。

通知格式与 Windows JobNotification 对齐：
- is_crash() / is_exit() / is_spawn() 方法
- type / pid / exit_code 属性
"""

import os
import signal
import logging
import threading
import time
from typing import Optional, List

_logger = logging.getLogger("pty-unix-process")


class UnixNotification:
    """Unix 进程通知（与 Windows JobNotification 接口对齐）"""

    def __init__(self, msg_type: str, pid: int, exit_code: Optional[int] = None):
        self._type = msg_type
        self._pid = pid
        self._exit_code = exit_code

    @property
    def type(self) -> str:
        return self._type

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def exit_code(self) -> Optional[int]:
        return self._exit_code

    def is_crash(self) -> bool:
        return self._type == "process_crash"

    def is_exit(self) -> bool:
        return self._type == "process_exit"

    def is_spawn(self) -> bool:
        return self._type == "process_spawn"

    def __repr__(self):
        return f"UnixNotification({self._type}, pid={self._pid}, exit_code={self._exit_code})"


class UnixProcessMonitor:
    """Unix 进程树监控器（基于 process group）

    用法:
        monitor = UnixProcessMonitor(child_pid)
        monitor.kill_tree()           # 杀整个进程树
        alive = monitor.is_alive()    # 检测进程树是否存活
        pids = monitor.get_process_list()  # 获取同 pgid 所有 PID
        notifications = monitor.drain_notifications()  # 获取通知
    """

    def __init__(self, child_pid: int):
        self._child_pid = child_pid
        self._crash_event = threading.Event()
        self._crash_exit_code: Optional[int] = None
        self._reaped = False
        self._exit_code: Optional[int] = None
        self._last_pids: List[int] = []
        self._notifications: List[UnixNotification] = []
        self._lock = threading.Lock()
        try:
            self._pgid = os.getpgid(child_pid)
        except OSError:
            self._pgid = child_pid
        _logger.info("UnixProcessMonitor: pid=%d pgid=%d", child_pid, self._pgid)

    @property
    def pgid(self) -> int:
        return self._pgid

    @property
    def crash_event(self) -> threading.Event:
        return self._crash_event

    @property
    def crash_exit_code(self) -> Optional[int]:
        return self._crash_exit_code

    def is_alive(self) -> bool:
        try:
            os.killpg(self._pgid, 0)
            return True
        except OSError:
            return False

    def is_child_alive(self) -> bool:
        try:
            os.kill(self._child_pid, 0)
            return True
        except OSError:
            return False

    def kill_tree(self, timeout: float = 3.0):
        try:
            os.killpg(self._pgid, signal.SIGTERM)
            _logger.info("kill_tree: SIGTERM sent to pgid=%d", self._pgid)
        except OSError as e:
            _logger.debug("kill_tree: SIGTERM failed (process may have exited): %s", e)
            try:
                os.kill(self._child_pid, signal.SIGTERM)
            except OSError:
                pass
            return

        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_alive():
                _logger.info("kill_tree: process group exited after SIGTERM")
                return
            time.sleep(0.1)

        try:
            os.killpg(self._pgid, signal.SIGKILL)
            _logger.info("kill_tree: SIGKILL sent to pgid=%d", self._pgid)
        except OSError:
            pass

    def get_process_list(self) -> List[int]:
        pids = self._get_pids_procfs()
        if pids is not None:
            return pids
        pids = self._get_pids_ps()
        if pids is not None:
            return pids
        if self.is_child_alive():
            return [self._child_pid]
        return []

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
                    parts = content.split(" ")
                    if len(parts) >= 5:
                        pid = int(parts[0])
                        pgrp = int(parts[4])
                        if pgrp == self._pgid:
                            pids.append(pid)
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

    def drain_notifications(self) -> List[UnixNotification]:
        self._detect_process_changes()
        self._check_crash_internal()
        with self._lock:
            notifications = self._notifications[:]
            self._notifications.clear()
            return notifications

    def _detect_process_changes(self):
        current = set(self.get_process_list())
        with self._lock:
            previous = set(self._last_pids)
        new_pids = current - previous
        gone_pids = previous - current
        with self._lock:
            for pid in new_pids:
                self._notifications.append(
                    UnixNotification("process_spawn", pid)
                )
            for pid in gone_pids:
                self._notifications.append(
                    UnixNotification("process_exit", pid)
                )
            self._last_pids = list(current)

    def _check_crash_internal(self):
        if self._reaped:
            return
        try:
            pid, status = os.waitpid(self._child_pid, os.WNOHANG)
            if pid == 0:
                return
            self._reaped = True
            exit_code = self._extract_exit_code(status)
            if exit_code is not None:
                self._exit_code = exit_code
                if exit_code != 0:
                    self._crash_exit_code = exit_code
                    self._crash_event.set()
                    with self._lock:
                        self._notifications.append(
                            UnixNotification("process_crash", pid, exit_code)
                        )
                else:
                    with self._lock:
                        self._notifications.append(
                            UnixNotification("process_exit", pid, exit_code)
                        )
        except ChildProcessError:
            self._reaped = True
        except Exception:
            pass

    @staticmethod
    def _extract_exit_code(status: int) -> Optional[int]:
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        return None

    def close(self):
        pass
