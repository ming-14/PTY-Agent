"""执行上下文 —— HandlerContext（daemon 服务器与 workflow 共用）。

从 daemon/handlers/base.py 移出：workflow 与 daemon handlers 都依赖该上下文，
置于执行层避免 workflow → daemon 的包级依赖（消除 daemon⇄workflow 循环）。
"""

from typing import Optional

from ..auth.base import Authenticator
from ..session.manager import SessionManager


class HandlerContext:
    __slots__ = ("authenticator", "manager", "server")

    def __init__(
        self,
        manager: SessionManager,
        authenticator: Optional[Authenticator] = None,
        server=None,
    ):
        self.manager = manager
        self.authenticator = authenticator
        self.server = server