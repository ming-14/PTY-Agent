"""事件历史管理器 — 进程/GUI 事件的队列、历史记录与查询

管理所有 PendingEvent 的:
- 实时添加到待处理队列（由 ProcessMonitor / GUI 检测调用）
- 消费并移入历史记录（consume_all）
- 全量查询与过滤（get_all）
- 存在性检测（check_existence）
- 线程安全（内部锁）
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional

from ..config.common import IS_WINDOWS

_logger = logging.getLogger("pty-session")


@dataclass
class PendingEvent:
    """待处理事件 — 进程创建/退出、GUI 窗口出现等"""
    timestamp: float
    type: str
    pid: int = 0
    info: str = ""
    hwnd: int = 0
    detail: dict = None


class EventHistoryManager:
    """事件历史管理器（线程安全）"""

    def __init__(self):
        self._pending: List[PendingEvent] = []
        self._history: List[PendingEvent] = []
        self._lock = threading.Lock()
        self._listeners: List[Callable[[dict], None]] = []

    def add_event(self, ev: PendingEvent):
        with self._lock:
            self._pending.append(ev)
            dicts = _events_to_dicts([ev])
            for listener in self._listeners:
                try:
                    listener(dicts[0] if dicts else {})
                except Exception:
                    pass
        _logger.debug("add_event: type=%s pid=%s hwnd=0x%X info=%r",
                      ev.type, ev.pid, ev.hwnd, ev.info[:80] if ev.info else "")

    def add_events(self, events: List[PendingEvent]):
        with self._lock:
            self._pending.extend(events)
        _logger.debug("add_events: count=%d", len(events))

    def consume_all(self) -> List[dict]:
        with self._lock:
            events = list(self._pending)
            self._pending.clear()
            self._history.extend(events)
        _logger.debug("consume_all: consumed %d events (history=%d)", len(events), len(self._history))
        return _events_to_dicts(events)

    def peek_pending(self) -> List[dict]:
        """查看当前待处理事件（不消费，不移入历史记录）"""
        with self._lock:
            events = list(self._pending)
        _logger.debug("peek_pending: %d pending events", len(events))
        return _events_to_dicts(events)

    def get_all(self, last: Optional[int] = None,
                since: Optional[float] = None,
                until: Optional[float] = None) -> List[dict]:
        with self._lock:
            all_ev = list(self._history) + list(self._pending)

        if since is not None:
            all_ev = [e for e in all_ev if e.timestamp >= since]
        if until is not None:
            all_ev = [e for e in all_ev if e.timestamp <= until]

        dicts = _events_to_dicts(all_ev)

        if last is not None and last > 0:
            dicts = dicts[-last:]

        return dicts

    def check_existence(self, ev: dict, pty_provider: Callable) -> bool:
        ev_type = ev.get("type", "")

        if ev_type in ("process_exit", "process_crash"):
            return False

        if ev_type == "process_spawn":
            pid = ev.get("pid", 0)
            if pid <= 0:
                return False
            pty = pty_provider()
            if not pty:
                return False
            try:
                pids = pty.get_process_list()
                return pid in pids
            except Exception:
                return False

        if ev_type == "gui_window":
            return _check_hwnd_exists(ev.get("hwnd", 0))

        return False

    def clear(self):
        with self._lock:
            self._pending.clear()
            self._history.clear()

    def add_event_listener(self, listener: Callable[[dict], None]):
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_event_listener(self, listener: Callable[[dict], None]):
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def history_count(self) -> int:
        with self._lock:
            return len(self._history)

    @property
    def pending_events(self) -> List[PendingEvent]:
        return self._pending

    @property
    def history_events(self) -> List[PendingEvent]:
        return self._history

    @property
    def lock(self) -> threading.Lock:
        return self._lock


def _events_to_dicts(events: List[PendingEvent]) -> List[dict]:
    result = []
    for e in events:
        dt = datetime.fromtimestamp(e.timestamp)
        iso_time = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 10000:02d}"
        d = {
            "time": iso_time,
            "type": e.type,
            "pid": e.pid,
        }
        if e.detail:
            d["detail"] = e.detail
        elif e.info:
            d["detail"] = {"info": e.info}
        if e.hwnd:
            d["hwnd"] = e.hwnd
        result.append(d)
    return result


def _check_hwnd_exists(hwnd: int) -> bool:
    if not hwnd or not IS_WINDOWS:
        return False
    import ctypes
    try:
        user32 = ctypes.windll.user32
        return bool(user32.IsWindow(ctypes.c_void_p(hwnd)))
    except Exception:
        return False
