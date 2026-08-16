from abc import ABC, abstractmethod
from typing import Optional

from ...auth.base import Authenticator
from ...session.manager import SessionManager
from ...logging import get_logger

_logger = get_logger("pty-daemon")


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


class DaemonHandler(ABC):
    @abstractmethod
    def handle(self, ctx: HandlerContext, conn, msg: dict): ...
