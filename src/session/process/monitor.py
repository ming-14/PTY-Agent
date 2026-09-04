"""进程监控器 — IOCP 通知排空、崩溃检测

职责：
- 从 Job Object IOCP 通知队列获取实时进程事件（创建/退出/崩溃）
- 通过崩溃事件信号通知 Session（避免轮询）
- 进程名称缓存（进程退出后无法再查询）

与 Session 的协作方式：
- Session._monitor_loop 定期调用 drain_notifications()
- 检测到的进程/崩溃事件通过 event_sink（EventHistoryManager.add_event）发送
- 崩溃事件通过 crash_event（threading.Event）信号即时通知 Session
"""

import time
import logging
import threading
from threading import Event
from typing import Callable, Dict, Optional

from ...pty.errors import STILL_ACTIVE, translate_exit_code
from .info import _get_process_name
from ..output.events import PendingEvent

_logger = logging.getLogger("pty-session")


class ProcessMonitor:
    """进程监控器

    通过 Job Object IOCP 通知队列检测进程创建/退出/崩溃，
    进程名称缓存供退出后查询使用。
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

        # 进程名称缓存（多线程访问，用锁保护）
        self._process_names: Dict[int, str] = {}
        self._names_lock = threading.Lock()

        # 崩溃事件信号
        self._crash_event = Event()

    # ── 公开方法 ──

    def _emit_process_end(self, pid: int, exit_code: Optional[int], now: float,
                          source: str):
        """统一处理进程退出/崩溃事件：从缓存取名称并发布事件

        Args:
            pid:        退出的进程 PID。
            exit_code:  退出码（None 表示未知/仍在运行）。
            now:        事件时间戳（time.time()）。
            source:     事件来源标识（用于日志，如 "IOCP"）。
        """
        # 优先取缓存名称；未命中才查询（避免 dict.pop 的默认值参数被急切求值）
        with self._names_lock:
            name = self._process_names.pop(pid, None)
        if name is None:
            name = _get_process_name(pid)
        if (exit_code is not None and exit_code != 0
                and exit_code != STILL_ACTIVE):
            crash_desc = translate_exit_code(exit_code)
            _logger.info(
                "ProcessMonitor(%s): crash pid=%d rc=%d (0x%08X) desc=%s",
                source, pid, exit_code, exit_code & 0xFFFFFFFF, crash_desc)
            self._event_sink(PendingEvent(
                timestamp=now, type="process_crash", pid=pid,
                info=(
                    f"{name} crashed!"
                    f" exit={exit_code} (0x{exit_code & 0xFFFFFFFF:08X})"
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

    def drain_notifications(self):
        """从 Job Object 的 IOCP 通知队列取出实时进程事件

        唯一的事件来源：Windows 后端通过 Job Object IOCP 实时推送
        进程创建/退出/崩溃事件；Unix / subprocess 后端返回空列表。
        """
        pty = self._pty_provider()
        if not pty:
            return
        try:
            notifs = pty.get_job_notifications()
        except (AttributeError, Exception):
            return
        if not notifs:
            return

        now = time.time()
        for n in notifs:
            if n.is_spawn() and n.pid:
                with self._names_lock:
                    self._process_names[n.pid] = _get_process_name(n.pid)
                    display = self._process_names.get(n.pid, f"PID {n.pid}")
                self._event_sink(PendingEvent(
                    timestamp=now, type="process_spawn", pid=n.pid,
                    info=display,
                ))
            elif n.is_exit() or n.is_crash():
                self._emit_process_end(n.pid, n.exit_code, now, "IOCP")

    def reset(self):
        """重置状态（在 Session.start 时调用）"""
        with self._names_lock:
            self._process_names.clear()
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
        with self._names_lock:
            return dict(self._process_names)
