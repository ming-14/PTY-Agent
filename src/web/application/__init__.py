"""应用层（Application Layer）。

包含用例（use cases）、端口（ports）以及跨用例服务。
该层只依赖领域层，不依赖具体的基础设施或框架实现。
"""

from .dispatcher import MessageDispatcher
from .handlers import build_handler_registry
from .ports import (
    ConnectionContext,
    EventPublisher,
    HistoryRepository,
    OutboundMessageChannel,
    SessionRepository,
    ShellProvider,
    SystemStatsProvider,
)
from .services import MessageEncoderService, SubscriptionService

__all__ = [
    "ConnectionContext",
    "EventPublisher",
    "HistoryRepository",
    "MessageDispatcher",
    "MessageEncoderService",
    "OutboundMessageChannel",
    "SessionRepository",
    "ShellProvider",
    "SubscriptionService",
    "SystemStatsProvider",
    "build_handler_registry",
]
