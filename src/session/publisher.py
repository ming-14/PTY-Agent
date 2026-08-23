"""SessionPublisher — 订阅者与结束回调管理

将 Session 的发布-订阅职责提取为独立组件：
- 输出订阅者管理（Web WS 实时推送）
- 会话结束回调管理（Web WS 等外部组件监听）
"""

from ..logging import get_logger
import threading
from typing import Callable, List

_logger = get_logger("pty-session")


class SessionPublisher:
    """会话发布器 — 管理输出订阅者和结束回调"""

    def __init__(self):
        self._subscribers: List = []
        # 订阅者快照：订阅/退订时重建，通知时直接复用（免每块 list 拷贝）
        self._subs_snapshot: List = []
        self._sub_lock = threading.Lock()
        self._on_end_callbacks: List[Callable] = []
        self._on_end_snapshot: List[Callable] = []
        self._on_end_lock = threading.Lock()
        # 尺寸变更回调（程序/客户端发起 resize 后广播，web 端立即响应）
        self._on_resized_callbacks: List[Callable] = []
        self._on_resized_snapshot: List[Callable] = []
        self._on_resized_lock = threading.Lock()

    def subscribe(self, callback):
        """注册输出订阅者"""
        with self._sub_lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)
                self._subs_snapshot = list(self._subscribers)

    def unsubscribe(self, callback):
        """移除输出订阅者"""
        with self._sub_lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass
            else:
                self._subs_snapshot = list(self._subscribers)

    def add_on_end_callback(self, callback: Callable):
        """注册会话结束回调"""
        with self._on_end_lock:
            if callback not in self._on_end_callbacks:
                self._on_end_callbacks.append(callback)
                self._on_end_snapshot = list(self._on_end_callbacks)

    def remove_on_end_callback(self, callback: Callable):
        """移除会话结束回调"""
        with self._on_end_lock:
            try:
                self._on_end_callbacks.remove(callback)
            except ValueError:
                pass
            else:
                self._on_end_snapshot = list(self._on_end_callbacks)

    def notify_subscribers(self, data: bytes, stream: str):
        """通知所有输出订阅者"""
        for cb in self._subs_snapshot:
            try:
                cb(data, stream)
            except Exception:
                pass

    def notify_end(self, session):
        """通知所有结束回调"""
        for cb in self._on_end_snapshot:
            try:
                cb(session)
            except Exception:
                pass

    def add_on_resized_callback(self, callback: Callable):
        """注册尺寸变更回调（web 端订阅，收到后广播 session_resized）"""
        with self._on_resized_lock:
            if callback not in self._on_resized_callbacks:
                self._on_resized_callbacks.append(callback)
                self._on_resized_snapshot = list(self._on_resized_callbacks)

    def remove_on_resized_callback(self, callback: Callable):
        """移除尺寸变更回调"""
        with self._on_resized_lock:
            try:
                self._on_resized_callbacks.remove(callback)
            except ValueError:
                pass
            else:
                self._on_resized_snapshot = list(self._on_resized_callbacks)

    def notify_resized(self, session, cols: int, rows: int, snapshot: str = ""):
        """通知所有尺寸变更回调（程序/客户端发起 resize 后调用）"""
        for cb in self._on_resized_snapshot:
            try:
                cb(session, cols, rows, snapshot)
            except Exception:
                pass
