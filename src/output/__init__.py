"""输出子包 — 缓冲区、触发匹配与事件历史"""

from .buffer import OutputBuffer
from .trigger import TriggerMatcher, safe_regex_search
from .events import EventHistoryManager, PendingEvent, _events_to_dicts
