"""Web 模块的领域层（Domain Layer）。

包含与 WebSocket 消息、会话、历史记录等相关的实体与值对象。
该层不依赖任何外部框架或基础设施实现。
"""

from .entities import (
    ActiveSession,
    HistoryDetail,
    HistorySession,
    OutputChunk,
    SessionEndedInfo,
    SessionEvent,
    SystemStats,
)

__all__ = [
    "ActiveSession",
    "HistoryDetail",
    "HistorySession",
    "OutputChunk",
    "SessionEndedInfo",
    "SessionEvent",
    "SystemStats",
]
