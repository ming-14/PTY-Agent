"""请求处理器 — RequestHandler

处理单个客户端连接的消息派发与业务逻辑。
实际处理逻辑已拆分到 handlers/ 子包中的独立 handler 类，
本模块仅保留 RequestHandler 入口类以兼容现有调用。
新增命令时在 handlers/ 子包中添加 handler 类并在 dispatcher 中注册。
"""

from ..auth.context import AuthContext
from ..session.manager import SessionManager
from .handlers.dispatcher import DaemonDispatcher


class RequestHandler:
    """处理单个客户端连接请求 — 委托给 DaemonDispatcher

    AuthContext 由 Listener 传入，包含该端口的签名器与认证器配置。
    """

    def __init__(self, manager: SessionManager, auth_context: AuthContext, server=None):
        self._dispatcher = DaemonDispatcher(manager, auth_context, server)

    def handle(self, conn, addr):
        self._dispatcher.handle(conn, addr)
