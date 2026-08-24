from abc import ABC, abstractmethod

from ...execution.context import HandlerContext


class DaemonHandler(ABC):
    @abstractmethod
    def handle(self, ctx: HandlerContext, conn, msg: dict): ...