"""输出子包 — 缓冲区、触发匹配、事件历史与输出管线

本包是 session 层的输出侧设施，对外只暴露以下名字。
"""

from .buffer import OutputBuffer
from .trigger import TriggerMatcher, safe_regex_search
from .events import EventHistoryManager, PendingEvent, format_timestamp_iso
from .screen import ScrollbackScreen
from .pipeline import StreamPipeline, ScreenPipeline

__all__ = [
    "OutputBuffer",
    "TriggerMatcher",
    "safe_regex_search",
    "EventHistoryManager",
    "PendingEvent",
    "format_timestamp_iso",
    "ScrollbackScreen",
    "StreamPipeline",
    "ScreenPipeline",
]
