"""GUI 窗口检测模块

管理 Job 进程树中 GUI 窗口的轮询检测，节流控制检测频率。
当检测到新窗口时，通过事件接收器发布 gui_window 事件。
"""

import time
import logging
import threading
from typing import List

from ..output.events import PendingEvent

_logger = logging.getLogger("pty-session")


class GuiDetector:
    """GUI 窗口检测器

    轮询 PTY 后端检测 Job 进程树中新增的 GUI 窗口，
    节流 2s 避免高频检测。检测到新窗口时通过 event_sink 发布事件。

    Attributes:
        gui_windows: 已检测到的 GUI 窗口列表（线程安全写入）。
        processes:   当前进程树 PID 列表（线程安全写入）。
    """

    def __init__(self, event_sink):
        """初始化 GuiDetector

        Args:
            event_sink: 事件接收回调（通常为 EventHistoryManager.add_event）。
        """
        self.gui_windows: List[dict] = []
        self.processes: List[int] = []
        self._detected_event = threading.Event()
        self._lock = threading.Lock()
        self._last_poll_ms = 0.0
        self._event_sink = event_sink

    def check(self, pty, session_id: str) -> None:
        """轮询检测 Job 进程树中新增的 GUI 窗口（节流 2s）

        Args:
            pty:        PTY 后端实例（提供 poll_gui_windows / get_process_list）。
            session_id: 会话 ID，用于日志。
        """
        now = time.monotonic()
        if now - self._last_poll_ms < 2.0:
            return
        self._last_poll_ms = now

        if not pty:
            return
        try:
            new_windows = pty.poll_gui_windows()
            if new_windows:
                with self._lock:
                    self.gui_windows.extend(new_windows)
                self._detected_event.set()
                _logger.info(
                    "会话 '%s' 检测到 %d 个新 GUI 窗口",
                    session_id, len(new_windows))
                ev_now = time.time()
                for w in new_windows:
                    self._event_sink(PendingEvent(
                        timestamp=ev_now, type="gui_window",
                        pid=w.get("pid", 0),
                        info=w.get("title", ""),
                        hwnd=w.get("hwnd", 0),
                    ))
            # 更新进程树信息
            pids = pty.get_process_list()
            if pids:
                with self._lock:
                    self.processes = pids
        except Exception as e:
            _logger.debug(
                "GUI 窗口检测异常 (会话 '%s'): %s", session_id, e)

    def clear(self) -> None:
        """重置 GUI 检测状态"""
        self.gui_windows = []
        self.processes = []
        self._detected_event.clear()
        self._last_poll_ms = 0.0

    @property
    def detected_event(self) -> threading.Event:
        """GUI 窗口检测信号（新窗口到达时被设置）"""
        return self._detected_event
