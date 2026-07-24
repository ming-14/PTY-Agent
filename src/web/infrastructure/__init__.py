"""基础设施层（Infrastructure Layer）。

包含领域层和应用层端口的具体实现，如仓储适配器、WebSocket 传输适配器、
系统统计提供者、VNC 远程桌面服务、FastScreen 屏幕查看服务等。
该层依赖应用层和领域层，以及外部框架/库。
"""

from .repositories.session_repository_adapter import SessionRepositoryAdapter
from .repositories.history_repository_adapter import HistoryRepositoryAdapter
from .system.stats_provider import SystemStatsProviderImpl
from .system.shell_provider import ShellProviderImpl
from .web.fastapi_transport import FastAPIWebSocketTransport
from .web.connection_context import WebSocketConnectionContext
from .web.event_publisher import EventPublisherImpl
from .thread_executor import ThreadExecutorImpl
from ...vnc import VncAdapter
from ...fastscreen import FastScreenAdapter

__all__ = [
    "SessionRepositoryAdapter",
    "HistoryRepositoryAdapter",
    "SystemStatsProviderImpl",
    "ShellProviderImpl",
    "FastAPIWebSocketTransport",
    "WebSocketConnectionContext",
    "EventPublisherImpl",
    "ThreadExecutorImpl",
    "VncAdapter",
    "FastScreenAdapter",
]
