"""GUI 窗口检测模块

管理 Job 进程树中 GUI 窗口的检测，并把结果作为**返回条件**上报。

两条通道，延迟量级不同：

- **事件通道（即时）**：后端 WinEvent hook 在窗口显示的瞬间检出并回调
  `_on_push`（运行在 GUI 消息泵线程上），取走结果、置边沿、`wake.notify()`
  唤醒等待循环 —— 与触发匹配同量级，无需等下一个轮询周期。
- **兜底扫描（节流）**：`poll_gui_windows()` 走 EnumWindows 全量扫描，
  覆盖"窗口在 hook 装载完成前就已显示"的竞态，并自愈任何漏投的事件。

`check()` 同时跑这两条通道；等待循环还可单独调 `drain_events()`
（无节流）以更低成本轮询事件队列。
"""

import time
import logging
import threading
from typing import List

from ..output.events import PendingEvent

_logger = logging.getLogger("pty-session")

# 兜底扫描周期（秒）：事件通道已覆盖常态，这里只兜竞态与漏投
_POLL_INTERVAL = 2.0


class GuiDetector:
    """GUI 窗口检测器

    检测后端 Job 进程树中新增的 GUI 窗口，
    兜底扫描按 `_POLL_INTERVAL` 节流。检测到新窗口时通过 event_sink
    发布事件、置位检测边沿，并经 wake 唤醒等待循环。

    Attributes:
        gui_windows: 已检测到的 GUI 窗口列表（线程安全写入）。
        processes:   当前进程树 PID 列表（线程安全写入）。
    """

    def __init__(self, event_sink, wake=None,
                 poll_interval: float = _POLL_INTERVAL):
        """初始化 GUI 窗口检测器

        Args:
            event_sink:    事件接收回调（通常为 EventHistoryManager.add_event）。
            wake:          共享唤醒锚（WakeSignal）；None 时退化为轮询发现。
            poll_interval: 兜底扫描节流周期（秒）。
        """
        self.gui_windows: List[dict] = []
        self.processes: List[int] = []
        self._detected_event = threading.Event()
        self._lock = threading.Lock()
        self._last_poll_ms = 0.0
        self._poll_interval = poll_interval
        self._event_sink = event_sink
        self._wake = wake
        # 已注册事件监听的后端实例（避免重复注册；后端重建后自动重挂）
        self._attached = None

    # ── 唤醒 / 发布 ────────────────────────────────────────────

    def _notify(self) -> None:
        """唤醒等待循环（须先发布状态再调用，见 WakeSignal 协议）"""
        if self._wake is not None:
            self._wake.notify()

    def _publish(self, windows: List[dict]) -> bool:
        """并入新窗口：写列表 + 置边沿 + 发事件 + 唤醒

        Args:
            windows: 窗口字典列表（hwnd/pid/title/class_name）。

        Returns:
            True 表示有新增窗口被并入。
        """
        if not windows:
            return False
        with self._lock:
            self.gui_windows.extend(windows)
        self._detected_event.set()
        _logger.info("检测到 %d 个新 GUI 窗口", len(windows))
        ev_now = time.time()
        for w in windows:
            self._event_sink(PendingEvent(
                timestamp=ev_now, type="gui_window",
                pid=w.get("pid", 0),
                info=w.get("title", ""),
                hwnd=w.get("hwnd", 0),
            ))
        self._notify()
        return True

    # ── 事件通道 ────────────────────────────────────────────────

    def _ensure_listener(self, backend) -> None:
        """向后端注册"有新窗口"回调（幂等；后端实例变化时重挂）"""
        if backend is None or self._attached is backend:
            return
        try:
            backend.set_gui_listener(self._on_push)
        except Exception as e:      # 不支持事件驱动的后端：仅靠兜底扫描
            _logger.debug("注册 GUI 事件监听失败: %s", e)
            self._attached = backend
            return
        self._attached = backend

    @staticmethod
    def _take_pending(backend) -> List[dict]:
        """取走事件队列中已检出的窗口（不支持时返回空）"""
        if backend is None:
            return []
        try:
            return backend.take_pending_gui_windows() or []
        except Exception:
            return []

    def _on_push(self) -> None:
        """（GUI 消息泵线程）事件到达即时通道：取走 + 发布 + 唤醒"""
        self._publish(self._take_pending(self._attached))

    def drain_events(self, backend) -> bool:
        """无节流地取走事件队列（等待循环每轮调用）

        Returns:
            True 表示本轮并入新窗口。
        """
        self._ensure_listener(backend)
        return self._publish(self._take_pending(backend))

    # ── 综合检测 ────────────────────────────────────────────────

    def check(self, backend, session_id: str, force: bool = False) -> bool:
        """检测新 GUI 窗口：事件通道（即时）+ 兜底扫描（节流）

        Args:
            backend:    后端实例（提供 poll_gui_windows / get_process_list）。
            session_id: 会话 ID，用于日志。
            force:      忽略兜底扫描节流，立即全量扫描一次。

        Returns:
            True 表示本轮发现新窗口。
        """
        if not backend:
            return False

        found = self.drain_events(backend)

        now = time.monotonic()
        if force or (now - self._last_poll_ms) >= self._poll_interval:
            self._last_poll_ms = now
            try:
                found |= self._publish(backend.poll_gui_windows())
            except Exception as e:
                _logger.debug(
                    "GUI 窗口检测异常 (会话 '%s'): %s", session_id, e)
            # 更新进程树信息
            try:
                pids = backend.get_process_list()
            except Exception:
                pids = None
            if pids:
                with self._lock:
                    self.processes = pids
        return found

    def clear(self) -> None:
        """重置 GUI 检测状态（窗口列表 + 边沿 + 扫描节流）"""
        with self._lock:
            self.gui_windows = []
            self.processes = []
        self._detected_event.clear()
        self._last_poll_ms = 0.0

    def arm(self) -> None:
        """建立本轮等待的 GUI 基线：丢弃残留的"新窗口"边沿信号。

        只清边沿，不清窗口列表 —— 已检出的窗口仍需出现在 debug 段供
        `closewin` 使用。清基线后，只有本轮新出现的窗口才能命中返回。
        """
        self._detected_event.clear()

    def consume_detection(self) -> bool:
        """判定 GUI 返回条件：命中则消费边沿并返回 True。

        边沿语义（一次性）保证同一窗口不会反复打断后续请求。
        """
        if self._detected_event.is_set():
            self._detected_event.clear()
            return True
        return False

    @property
    def detected_event(self) -> threading.Event:
        """GUI 窗口检测边沿信号（新窗口到达时被设置）

        等待路径请走 `arm()` / `consume_detection()`，不要直接消费此事件，
        以免绕过基线语义。
        """
        return self._detected_event

    def get_gui_windows(self) -> List[dict]:
        """线程安全获取 GUI 窗口列表（返回副本）"""
        with self._lock:
            return list(self.gui_windows)

    def get_processes(self) -> List[int]:
        """线程安全获取进程树 PID 列表（返回副本）"""
        with self._lock:
            return list(self.processes)
