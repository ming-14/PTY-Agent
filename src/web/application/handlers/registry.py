"""WebSocket 消息处理器注册表：消息类型 → 处理器映射。"""

from __future__ import annotations

from .base import MessageHandler
from .cursor import (
    CursorLocatorStartHandler,
    CursorLocatorStopHandler,
    CursorLocatorUpdateConfigHandler,
)
from .detail import SessionDetailHandler, SessionDetailRefreshHandler
from .history import DeleteHistoryHandler, HistoryDetailHandler, ListHistoryHandler
from .screenshare import FsBringToFrontHandler, FsListTargetsHandler, FsStatusHandler
from .session import (
    CreateSessionHandler,
    InputHandler,
    KeyInputHandler,
    KillSessionHandler,
    MouseInputHandler,
    ResizeHandler,
    SignalHandler,
    SubscribeSessionHandler,
    UnsubscribeSessionHandler,
)
from .size_mode import SetSizeModeHandler, TakeoverSizeControlHandler
from .system import ListSessionsHandler, ListShellsHandler, PingHandler, SystemStatsHandler
from .vnc import VncStartHandler, VncStatusHandler, VncStopHandler


def build_handler_registry() -> dict[str, MessageHandler]:
    """构建消息类型到处理器的映射。"""
    return {
        "ping": PingHandler(),
        "list": ListSessionsHandler(),
        "shells": ListShellsHandler(),
        "system_stats": SystemStatsHandler(),
        "history": ListHistoryHandler(),
        "history_detail": HistoryDetailHandler(),
        "create": CreateSessionHandler(),
        "subscribe": SubscribeSessionHandler(),
        "unsubscribe": UnsubscribeSessionHandler(),
        "input": InputHandler(),
        "key": KeyInputHandler(),
        "mouse": MouseInputHandler(),
        "signal": SignalHandler(),
        "resize": ResizeHandler(),
        "kill": KillSessionHandler(),
        "delete_history": DeleteHistoryHandler(),
        "session_detail": SessionDetailHandler(),
        "session_detail_refresh": SessionDetailRefreshHandler(),
        "vnc_status": VncStatusHandler(),
        "vnc_start": VncStartHandler(),
        "vnc_stop": VncStopHandler(),
        "fs_status": FsStatusHandler(),
        "fs_list_targets": FsListTargetsHandler(),
        "fs_bring_to_front": FsBringToFrontHandler(),
        "cursor_locator_start": CursorLocatorStartHandler(),
        "cursor_locator_stop": CursorLocatorStopHandler(),
        "cursor_locator_update_config": CursorLocatorUpdateConfigHandler(),
        # 自适应排他锁
        "takeover_size_control": TakeoverSizeControlHandler(),
        "set_size_mode": SetSizeModeHandler(),
    }