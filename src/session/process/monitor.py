"""进程监控器 — 进程树 diff、IOCP 通知排空、崩溃检测

职责：
- 比较 PID 快照检测新增/退出进程
- 从 Job Object IOCP 通知队列获取实时进程事件
- 通过崩溃事件信号通知 Session（避免轮询）
- 进程名称缓存（进程退出后无法再查询）

与 Session 的协作方式：
- Session._monitor_loop 定期调用 drain_notifications() 和 check_events()
- 检测到的进程/崩溃事件通过 event_sink（EventHistoryManager.add_event）发送
- 崩溃事件通过 crash_event（threading.Event）信号即时通知 Session
"""

import time
import logging
from threading import Event
from typing import Callable, Dict, List, Optional, Set

from ...config import IS_WINDOWS
from .info import _get_process_name
from ..output.events import PendingEvent

_logger = logging.getLogger("pty-session")

if IS_WINDOWS:
    from ...pty.windows.error_msg import (
        STILL_ACTIVE, translate_windows_error,
    )


class ProcessMonitor:
    """进程监控器

    管理进程列表快照、进程名称缓存，通过 IOCP 和 PID diff 两种
    方式检测进程创建/退出/崩溃。
    """

    def __init__(
        self,
        pty_provider: Callable,
        event_sink: Callable[[PendingEvent], None],
    ):
        """
        Args:
            pty_provider: 返回当前 PTY 实例的可调用对象（lambda: self._pty）。
            event_sink:   添加 PendingEvent 的回调（EventHistoryManager.add_event）。
        """
        self._pty_provider = pty_provider
        self._event_sink = event_sink

        # 进程追踪状态
        self._last_pid_snapshot: Set[int] = set()
        self._process_names: Dict[int, str] = {}
        self._last_process_check_ms: float = 0.0

        # 崩溃事件信号
        self._crash_event = Event()

    # ── 公开方法 ──

    def drain_notifications(self):
        """从 Job Object 的 IOCP 通知队列取出实时进程事件

        与 check_events 互补：IOCP 提供实时崩溃通知，
        进程列表比较提供创建/退出信息。
        """
        pty = self._pty_provider()
        if not pty:
            return
        try:
            notifs = pty.get_job_notifications()
        except AttributeError:
            return
        except Exception:
            return
        if not notifs:
            return

        now = time.time()
        for n in notifs:
            if n.is_spawn() and n.pid:
                self._process_names[n.pid] = _get_process_name(n.pid)
                display = self._process_names.get(n.pid, f"PID {n.pid}")
                self._event_sink(PendingEvent(
                    timestamp=now, type="process_spawn", pid=n.pid,
                    info=display,
                ))
            elif n.is_exit() or n.is_crash():
                name = self._process_names.pop(
                    n.pid, _get_process_name(n.pid))
                rc = n.exit_code
                if rc is not None and rc != 0 and rc != STILL_ACTIVE:
                    crash_desc = translate_windows_error(rc)
                    _logger.info(
                        "IOCP crash pid=%d rc=%d (0x%08X) desc=%s",
                        n.pid, rc, rc & 0xFFFFFFFF, crash_desc)
                    self._event_sink(PendingEvent(
                        timestamp=now, type="process_crash", pid=n.pid,
                        info=(
                            f"{name} crashed!"
                            f" exit={rc} (0x{rc & 0xFFFFFFFF:08X})"
                            f"\n  → {crash_desc}"
                        ),
                    ))
                    self._crash_event.set()
                elif rc is not None:
                    self._event_sink(PendingEvent(
                        timestamp=now, type="process_exit", pid=n.pid,
                        info=f"{name} exited (exit={rc})",
                    ))
                else:
                    self._event_sink(PendingEvent(
                        timestamp=now, type="process_exit", pid=n.pid,
                        info=f"{name} exited (unknown)",
                    ))

    def check_events(self):
        """比较进程列表快照，检测新增/退出的进程

        性能：节流到最多每 2s 执行一次。
        对消失的 PID 查询退出码以判断是否崩溃。
        """
        now_ms = time.monotonic()
        if now_ms - self._last_process_check_ms < 2.0:
            if not self._crash_event.is_set():
                return
        self._last_process_check_ms = now_ms

        pty = self._pty_provider()
        try:
            current_pids = set(pty.get_process_list()) if pty else set()
        except Exception:
            return
        old_pids = self._last_pid_snapshot
        if not old_pids and not current_pids:
            return

        new_pids = current_pids - old_pids
        gone_pids = old_pids - current_pids

        if new_pids or gone_pids:
            _logger.debug("ProcessMonitor: process change new=%s gone=%s",
                          new_pids, gone_pids)

        now = time.time()
        for pid in new_pids:
            name = _get_process_name(pid)
            self._process_names[pid] = name
            self._event_sink(PendingEvent(
                timestamp=now, type="process_spawn", pid=pid,
                info=name,
            ))

        for pid in gone_pids:
            name = self._process_names.pop(pid, _get_process_name(pid))
            exit_code = None
            if pty:
                try:
                    exit_code = pty.get_child_process_exit_code(pid)
                except Exception:
                    pass
            if (exit_code is not None and exit_code != 0
                    and exit_code != STILL_ACTIVE):
                crash_desc = translate_windows_error(exit_code)
                _logger.info(
                    "ProcessMonitor: crash pid=%d exit_code=%d (0x%08X) "
                    "desc=%s",
                    pid, exit_code, exit_code & 0xFFFFFFFF, crash_desc)
                self._event_sink(PendingEvent(
                    timestamp=now, type="process_crash", pid=pid,
                    info=(
                        f"{name} crashed!"
                        f" exit={exit_code}"
                        f" (0x{exit_code & 0xFFFFFFFF:08X})"
                        f"\n  → {crash_desc}"
                    ),
                ))
                self._crash_event.set()
            else:
                exit_str = (
                    f"exited (exit={exit_code})"
                    if exit_code is not None
                    else "exited (unknown)"
                )
                self._event_sink(PendingEvent(
                    timestamp=now, type="process_exit", pid=pid,
                    info=f"{name} {exit_str}",
                ))

        self._last_pid_snapshot = current_pids

    def reset(self, initial_pids: Optional[Set[int]] = None):
        """重置状态（在 Session.start 时调用）

        Args:
            initial_pids: 初始 PID 快照。None 表示空集合。
        """
        self._last_pid_snapshot = initial_pids or set()
        self._process_names.clear()
        self._last_process_check_ms = 0.0
        self._crash_event.clear()

    # ── 属性 ──

    @property
    def crash_event(self) -> Event:
        return self._crash_event

    def clear_crash(self):
        """清除崩溃事件信号"""
        self._crash_event.clear()

    @property
    def process_names(self) -> Dict[int, str]:
        return self._process_names
