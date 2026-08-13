"""进程监控器 — 进程树 diff、tracker 通知排空、崩溃检测

职责：
- 从 ProcessTreeTracker 排空统一通知（IOCP 推送 / Unix 轮询 diff）
- 比较 PID 快照检测新增/退出进程（兜底）
- 崩溃检测：退出码非 0 且非 STILL_ACTIVE → process_crash 事件 + crash_event 信号
- 进程名称缓存（进程退出后无法再查询）

与 Session 的协作方式：
- Session._monitor_loop 定期调用 drain_notifications() 和 check_events()
- 检测到的进程/崩溃事件通过 event_sink（EventHistoryManager.add_event）发送
- 崩溃事件通过 crash_event（threading.Event）信号即时通知 Session

依赖方向：process/monitor → process/base（ProcessTreeTracker 抽象），
不感知具体实现（Job / pgid / 未来 sandbox 委派）。
"""

import time
import logging
from threading import Event
from typing import Callable, Dict, List, Optional, Set

from ..config.common import IS_WINDOWS
from .info import _get_process_name, _get_process_detail
from .base import ProcessTreeTracker
from ..output.events import PendingEvent

_logger = logging.getLogger("pty-session")

if IS_WINDOWS:
    from .win32_error import (
        STILL_ACTIVE, translate_windows_error,
    )


class ProcessMonitor:
    """进程监控器

    管理进程列表快照、进程名称缓存，通过 tracker 通知和 PID diff 两种
    方式检测进程创建/退出/崩溃。
    """

    def __init__(
        self,
        tracker: ProcessTreeTracker,
        event_sink: Callable[[PendingEvent], None],
    ):
        """
        Args:
            tracker:   进程树追踪器（ProcessTreeTracker 抽象端口）。
            event_sink: 添加 PendingEvent 的回调（EventHistoryManager.add_event）。
        """
        self._tracker = tracker
        self._event_sink = event_sink

        # 进程追踪状态
        self._last_pid_snapshot: Set[int] = set()
        self._process_names: Dict[int, str] = {}
        self._process_details: Dict[int, dict] = {}
        self._last_process_check_ms: float = 0.0
        self._iocp_exited_pids: Set[int] = set()

        # 崩溃事件信号
        self._crash_event = Event()

    # ── 公开方法 ──

    def drain_notifications(self):
        """从 tracker 排空实时进程事件（统一通知）

        Windows：IOCP 推送（含 name/path 尽力填充）
        Unix：进程列表 diff + waitpid 结果
        """
        try:
            notifs = self._tracker.drain_notifications()
        except Exception:
            return
        if not notifs:
            return

        now = time.time()
        for n in notifs:
            if n.is_spawn() and n.pid:
                name = n.process_name or ""
                path = n.process_path or ""
                if not name:
                    detail = _get_process_detail(n.pid) or {}
                    name = detail.get("name") or _get_process_name(n.pid)
                    path = detail.get("path", "")
                self._process_names[n.pid] = name
                self._event_sink(PendingEvent(
                    timestamp=now, type="process_spawn", pid=n.pid,
                    info=name,
                    detail={"name": name, "path": path} if name else None,
                ))
            elif n.is_exit() or n.is_crash():
                name = self._process_names.pop(
                    n.pid, _get_process_name(n.pid))
                self._iocp_exited_pids.add(n.pid)
                rc = n.exit_code
                if rc is not None and rc != 0 and rc != STILL_ACTIVE:
                    crash_desc = translate_windows_error(rc)
                    _logger.info(
                        "IOCP crash pid=%d rc=%d (0x%08X) desc=%s",
                        n.pid, rc, rc & 0xFFFFFFFF, crash_desc)
                    detail = {"exitCode": rc}
                    if crash_desc:
                        detail["errorMessage"] = crash_desc
                        info_msg = (
                            f"{name} crashed!"
                            f" exit={rc} (0x{rc & 0xFFFFFFFF:08X})"
                            f"\n  → {crash_desc}"
                        )
                    else:
                        info_msg = (
                            f"{name} crashed!"
                            f" exit={rc} (0x{rc & 0xFFFFFFFF:08X})"
                        )
                    self._event_sink(PendingEvent(
                        timestamp=now, type="process_crash", pid=n.pid,
                        info=info_msg,
                        detail=detail,
                    ))
                    self._crash_event.set()
                elif rc is not None:
                    self._event_sink(PendingEvent(
                        timestamp=now, type="process_exit", pid=n.pid,
                        info=f"{name} exited (exit={rc})",
                        detail={"exitCode": rc},
                    ))
                else:
                    self._event_sink(PendingEvent(
                        timestamp=now, type="process_exit", pid=n.pid,
                        info=f"{name} exited (unknown)",
                    ))

    def check_events(self, force=False):
        """比较进程列表快照，检测新增/退出的进程

        性能：节流到最多每 2s 执行一次。
        对消失的 PID 查询退出码以判断是否崩溃。

        Args:
            force: 为 True 时绕过节流，确保在 reader 退出路径中不遗漏事件。
        """
        now_ms = time.monotonic()
        if not force and now_ms - self._last_process_check_ms < 2.0:
            if not self._crash_event.is_set():
                return
        self._last_process_check_ms = now_ms

        try:
            current_pids = set(self._tracker.get_process_list())
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
            detail = _get_process_detail(pid) or {}
            name = detail.get("name") or _get_process_name(pid)
            self._process_names[pid] = name
            if detail:
                self._process_details[pid] = detail
            self._event_sink(PendingEvent(
                timestamp=now, type="process_spawn", pid=pid,
                info=name,
                detail=detail or None,
            ))

        for pid in gone_pids:
            if pid in self._iocp_exited_pids:
                self._iocp_exited_pids.discard(pid)
                continue
            name = self._process_names.pop(pid, _get_process_name(pid))
            cached_detail = self._process_details.pop(pid, None)
            exit_code = None
            try:
                exit_code = self._tracker.get_process_exit_code(pid)
            except Exception:
                pass
            if (exit_code is not None and exit_code != 0
                    and exit_code != STILL_ACTIVE):
                crash_desc = translate_windows_error(exit_code)
                _logger.info(
                    "ProcessMonitor: crash pid=%d exit_code=%d (0x%08X) "
                    "desc=%s",
                    pid, exit_code, exit_code & 0xFFFFFFFF, crash_desc)
                detail = {"exitCode": exit_code}
                if crash_desc:
                    detail["errorMessage"] = crash_desc
                    info_msg = (
                        f"{name} crashed!"
                        f" exit={exit_code}"
                        f" (0x{exit_code & 0xFFFFFFFF:08X})"
                        f"\n  → {crash_desc}"
                    )
                else:
                    info_msg = (
                        f"{name} crashed!"
                        f" exit={exit_code}"
                        f" (0x{exit_code & 0xFFFFFFFF:08X})"
                    )
                self._event_sink(PendingEvent(
                    timestamp=now, type="process_crash", pid=pid,
                    info=info_msg,
                    detail=detail,
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
                    detail={"exitCode": exit_code} if exit_code is not None else None,
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
