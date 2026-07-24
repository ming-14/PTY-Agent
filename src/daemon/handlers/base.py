import socket
import time
import logging
from abc import ABC, abstractmethod
from typing import Optional

from ...session.manager import SessionManager
from ...auth.base import Authenticator
from .utils import check_ended_session

_logger = logging.getLogger("pty-daemon")


class HandlerContext:
    __slots__ = ("manager", "authenticator", "server")

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
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        ...
