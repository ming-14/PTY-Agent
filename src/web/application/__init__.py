"""应用层（Application Layer）。

包含用例（use cases）、端口（ports）以及跨用例服务。
该层只依赖领域层，不依赖具体的基础设施或框架实现。
"""

from .ports import (
    SessionRepository,
    HistoryRepository,
    OutboundMessageChannel,
    SystemStatsProvider,
    ShellProvider,
    EventPublisher,
    ConnectionContext,
)
from .services import MessageEncoderService, SubscriptionService
from .dispatcher import MessageDispatcher
from .handlers import build_handler_registry

__all__ = [
    "SessionRepository",
    "HistoryRepository",
    "OutboundMessageChannel",
    "SystemStatsProvider",
    "ShellProvider",
    "EventPublisher",
    "ConnectionContext",
    "MessageEncoderService",
    "SubscriptionService",
    "MessageDispatcher",
    "build_handler_registry",
]
