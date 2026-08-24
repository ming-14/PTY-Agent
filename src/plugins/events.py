"""daemon 事件总线 — 进程内 pub/sub，主题段通配

主题按 "." 分段："*" 匹配单段，" >" 匹配剩余任意段（含 0 段，MQTT 风格）。
插件经清单 events.subscribe 声明订阅（注册表 enable 时挂钩），或运行期
ctx.events.subscribe 编程订阅；发布方不感知订阅者，事件对象不可变。
订阅回调异常隔离：只记日志不中断发布。

标准主题（source 为发布方，session 事件带 sessionId 载荷）：
    daemon.started / daemon.stopping
    plugin.enabled / plugin.disabled / plugin.installed / plugin.uninstalled
    session.created / session.ended / session.event.<事件类型>
"""

import threading
import time
from typing import Callable, List

from ..logging import get_logger

_logger = get_logger("pty-plugins")


class Event:
    """总线事件对象"""

    __slots__ = ("topic", "source", "timestamp", "payload")

    def __init__(self, topic: str, source: str, payload):
        self.topic = topic
        self.source = source
        self.timestamp = time.time()
        self.payload = payload or {}

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "source": self.source,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


def match_topic(pattern: str, topic: str) -> bool:
    """主题模式匹配："*" 匹配单段，">" 匹配剩余任意段（含 0 段）"""
    psegs = pattern.split(".")
    tsegs = topic.split(".")
    for i, seg in enumerate(psegs):
        if seg == ">":
            return True
        if i >= len(tsegs):
            return False
        if seg == "*":
            continue
        if seg != tsegs[i]:
            return False
    return len(psegs) == len(tsegs)


class EventBus:
    """进程内事件总线（线程安全）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs: List[tuple] = []  # (pattern, callback)

    def subscribe(self, pattern: str, callback) -> None:
        """订阅主题模式；回调签名 callback(event: Event)"""
        with self._lock:
            self._subs.append((pattern, callback))

    def unsubscribe(self, pattern: str, callback) -> None:
        with self._lock:
            self._subs[:] = [
                (p, cb) for p, cb in self._subs if not (p == pattern and cb is callback)
            ]

    def publish(self, topic: str, payload=None, source: str = "system") -> None:
        """发布事件（同步派发给所有匹配订阅者）"""
        event = Event(topic, source, payload)
        with self._lock:
            subs = list(self._subs)
        for pattern, callback in subs:
            if not match_topic(pattern, topic):
                continue
            try:
                callback(event)
            except Exception:
                _logger.exception(
                    "事件订阅回调异常 pattern=%s topic=%s", pattern, topic
                )