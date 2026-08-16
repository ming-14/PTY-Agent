"""FastAPI / Starlette WebSocket 传输适配器。"""

import json
from typing import Optional

from starlette.websockets import WebSocket, WebSocketDisconnect

from ...application.ports import OutboundMessageChannel


class WSMsgType:
    TEXT = "text"
    BINARY = "binary"
    CLOSE = "close"
    ERROR = "error"


class WSMessage:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


class FastAPIWebSocketTransport(OutboundMessageChannel):
    """将 FastAPI/Starlette WebSocket 包装为应用层 OutboundMessageChannel。

    同时提供类似 aiohttp WebSocketResponse 的异步迭代接口，供控制器消费消息。
    """

    def __init__(self, ws: WebSocket):
        self._ws = ws
        self._closed = False
        self._close_code: Optional[int] = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            msg = await self._ws.receive()
        except WebSocketDisconnect as exc:
            self._closed = True
            self._close_code = exc.code
            raise StopAsyncIteration
        except Exception:
            self._closed = True
            raise StopAsyncIteration

        if msg["type"] == "websocket.receive":
            if "text" in msg:
                return WSMessage(WSMsgType.TEXT, msg["text"])
            if "bytes" in msg:
                return WSMessage(WSMsgType.BINARY, msg["bytes"])
            return WSMessage(WSMsgType.ERROR, msg)
        if msg["type"] == "websocket.disconnect":
            self._closed = True
            self._close_code = msg.get("code")
            raise StopAsyncIteration
        return WSMessage(WSMsgType.ERROR, msg)

    @property
    def closed(self) -> bool:
        return self._closed

    async def send(self, message: dict) -> None:
        await self._ws.send_json(message)

    async def send_batch(self, messages: list) -> None:
        """批量发送：多条消息合并为一条 JSON 数组文本帧

        高频 output 推送时显著减少 WS 帧数（前端按数组逐条分发）。
        """
        await self._ws.send_text(json.dumps(messages, ensure_ascii=False))

    async def close(self, code: int = 1000) -> None:
        try:
            await self._ws.close(code=code)
        except Exception:
            pass
        self._closed = True
