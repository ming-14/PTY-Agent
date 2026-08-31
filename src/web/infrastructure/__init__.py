"""基础设施层（Infrastructure Layer）。

包含领域层和应用层端口的具体实现，如仓储适配器、WebSocket 传输适配器、
系统统计提供者、VNC 远程桌面服务、Screenshare 屏幕查看服务、认证会话存储等。
该层依赖应用层和领域层，以及外部框架/库。
"""

from .auth.session_store import SessionStore
from .cursor_locator_adapter import CursorLocatorAdapter
from .repositories.history_repository_adapter import HistoryRepositoryAdapter
from .repositories.session_repository_adapter import SessionRepositoryAdapter
from .system.shell_provider import ShellProviderImpl
from .system.stats_provider import SystemStatsProviderImpl
from .thread_executor import ThreadExecutorImpl
from .web.connection_context import WebSocketConnectionContext
from .web.event_publisher import EventPublisherImpl

# 注意：FastAPIWebSocketTransport 不在此导出（fastapi_transport 顶层引入 starlette，
# 而本包会被 daemon 核心的 history_store 探测链（get_history_store_cls）连带导入，
# 会导致 web 关闭时也加载 web 依赖）。由使用方（presentation/server.py）按需导入。

# 注意：VncAdapter / ScreenshareAdapter 属于可选模块（src/vnc、src/screenshare），
# 不在此模块级导入（避免目录缺失时整个 infrastructure 包导入失败）。
# 请通过 src/optional 网关获取（get_vnc_adapter_cls / get_screenshare_adapter_cls）。

__all__ = [
    "CursorLocatorAdapter",
    "EventPublisherImpl",
    "HistoryRepositoryAdapter",
    "SessionRepositoryAdapter",
    "SessionStore",
    "ShellProviderImpl",
    "SystemStatsProviderImpl",
    "ThreadExecutorImpl",
    "WebSocketConnectionContext",
]
