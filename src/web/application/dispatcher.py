"""WebSocket 消息分发器。"""

import logging
from typing import Optional

from ...protocol.response import Response
from .handlers import HandlerContext, MessageHandler, build_handler_registry

_logger = logging.getLogger("pty-web")


class MessageDispatcher:
    """根据消息类型将消息分派给对应的用例处理器。"""

    def __init__(self, registry: Optional[dict[str, MessageHandler]] = None):
        self._registry = registry or build_handler_registry()

    async def dispatch(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        """分发消息并返回响应消息列表。"""
        t = msg.get("type", "")
        sid = msg.get("session_id", "")
        _logger.debug("dispatch: type=%s sid=%r", t, sid)
        handler = self._registry.get(t)
        if not handler:
            _logger.warning("dispatch: unknown type=%s sid=%r", t, sid)
            return [Response.error(f"unknown type: {t}")]
        try:
            return await handler.handle(ctx, msg)
        except Exception as e:
            _logger.exception("dispatch %s error sid=%r", t, sid)
            return [Response.error(str(e))]
