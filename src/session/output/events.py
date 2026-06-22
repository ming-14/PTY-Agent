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

from ...config import IS_WINDOWS

_logger = logging.getLogger("pty-session")


@dataclass
class PendingEvent:
    """待处理事件 — 进程创建/退出、GUI 窗口出现等"""
    timestamp: float      # 事件发生时间 (time.time)
    type: str             # process_spawn / process_exit / gui_window
    pid: int = 0
    info: str = ""
    hwnd: int = 0


class EventHistoryManager:
    """事件历史管理器（线程安全）

    内部维护两个队列：
    - _pending: 尚未消费的新事件（ProcessMonitor/GUI 检测产生）
    - _history: 已消费的归档事件
    """

    def __init__(self):
        self._pending: List[PendingEvent] = []
        self._history: List[PendingEvent] = []
        self._lock = threading.Lock()

    # ── 写入 ──

    def add_event(self, ev: PendingEvent):
        """添加单个待处理事件"""
        with self._lock:
            self._pending.append(ev)
        _logger.debug("add_event: type=%s pid=%s hwnd=0x%X info=%r",
                      ev.type, ev.pid, ev.hwnd, ev.info[:80] if ev.info else "")

    def add_events(self, events: List[PendingEvent]):
        """批量添加待处理事件"""
        with self._lock:
            self._pending.extend(events)
        _logger.debug("add_events: count=%d", len(events))

    # ── 消费/查询 ──

    def consume_all(self) -> List[dict]:
        """消费所有待处理事件并移入历史

        Returns:
            事件字典列表（time/type/pid/info/hwnd）。
        """
        with self._lock:
            events = list(self._pending)
            self._pending.clear()
            self._history.extend(events)
        _logger.debug("consume_all: consumed %d events (history=%d)", len(events), len(self._history))
        return _events_to_dicts(events)

    def get_all(self, last: Optional[int] = None,
                since: Optional[float] = None,
                until: Optional[float] = None) -> List[dict]:
        """获取所有事件（待处理 + 历史），支持过滤

        Args:
            last:  仅返回最近 N 条。
            since: 仅返回时间 >= since 的事件（Unix 时间戳）。
            until: 仅返回时间 <= until 的事件（Unix 时间戳）。

        Returns:
            过滤后的事件字典列表（时间由远到近）。
        """
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
        """检测事件关联的进程/窗口是否仍然存在

        Args:
            ev:           事件字典（含 type/pid/hwnd）。
            pty_provider: 返回当前 PTY 实例的可调用对象。

        Returns:
            True 表示进程/窗口仍然存在。
        """
        ev_type = ev.get("type", "")

        # process_exit / process_crash：进程已退出，始终不存在
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
        """清空所有待处理事件和历史记录"""
        with self._lock:
            self._pending.clear()
            self._history.clear()

    # ── 属性 ──

    @property
    def pending_count(self) -> int:
        """待处理事件数量"""
        with self._lock:
            return len(self._pending)

    @property
    def history_count(self) -> int:
        """历史记录数量"""
        with self._lock:
            return len(self._history)

    @property
    def pending_events(self) -> List[PendingEvent]:
        """待处理事件列表引用（**仅在持锁时读取**）"""
        return self._pending

    @property
    def history_events(self) -> List[PendingEvent]:
        """历史事件列表引用（**仅在持锁时读取**）"""
        return self._history

    @property
    def lock(self) -> threading.Lock:
        return self._lock


def _events_to_dicts(events: List[PendingEvent]) -> List[dict]:
    """将 PendingEvent 对象列表转为字典列表

    time 转为 ISO 8601 格式（两位毫秒）。hwnd 为 0 时不输出。
    """
    result = []
    for e in events:
        dt = datetime.fromtimestamp(e.timestamp)
        iso_time = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 10000:02d}"
        d = {
            "time": iso_time,
            "type": e.type,
            "pid": e.pid,
            "info": e.info,
        }
        if e.hwnd:
            d["hwnd"] = e.hwnd
        result.append(d)
    return result


def _check_hwnd_exists(hwnd: int) -> bool:
    """检查窗口句柄是否仍然有效（Windows 专用）"""
    if not hwnd or not IS_WINDOWS:
        return False
    import ctypes
    try:
        user32 = ctypes.windll.user32
        return bool(user32.IsWindow(ctypes.c_void_p(hwnd)))
    except Exception:
        return False
