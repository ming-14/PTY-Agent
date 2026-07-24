"""SessionPublisher — 订阅者与结束回调管理

将 Session 的发布-订阅职责提取为独立组件：
- 输出订阅者管理（Web WS 实时推送）
- 会话结束回调管理（Web WS 等外部组件监听）
"""

import logging
import threading
from typing import Callable, List

_logger = logging.getLogger("pty-session")


class SessionPublisher:
    """会话发布器 — 管理输出订阅者和结束回调"""

    def __init__(self):
        self._subscribers: List = []
        self._sub_lock = threading.Lock()
        self._on_end_callbacks: List[Callable] = []
        self._on_end_lock = threading.Lock()

    def subscribe(self, callback):
        """注册输出订阅者"""
        with self._sub_lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback):
        """移除输出订阅者"""
        with self._sub_lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

    def add_on_end_callback(self, callback: Callable):
        """注册会话结束回调"""
        with self._on_end_lock:
            if callback not in self._on_end_callbacks:
                self._on_end_callbacks.append(callback)

    def remove_on_end_callback(self, callback: Callable):
        """移除会话结束回调"""
        with self._on_end_lock:
            try:
                self._on_end_callbacks.remove(callback)
            except ValueError:
                pass

    def notify_subscribers(self, data: bytes, stream: str):
        """通知所有输出订阅者"""
        with self._sub_lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(data, stream)
            except Exception:
                pass

    def notify_end(self, session):
        """通知所有结束回调"""
        with self._on_end_lock:
            callbacks = list(self._on_end_callbacks)
        for cb in callbacks:
            try:
                cb(session)
            except Exception:
                pass
