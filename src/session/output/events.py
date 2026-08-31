"""事件历史管理器 — 进程/GUI 事件的队列、历史记录与消费

管理所有 PendingEvent 的:
- 实时添加到待处理队列（由 ProcessMonitor / GUI 检测调用）
- 消费并移入历史记录（consume_all）
- 线程安全（内部锁）
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import List

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
