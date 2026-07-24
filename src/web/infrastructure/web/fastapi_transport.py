"""FastAPI / Starlette WebSocket 传输适配器。"""

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

    async def close(self, code: int = 1000) -> None:
        try:
            await self._ws.close(code=code)
        except Exception:
            pass
        self._closed = True
