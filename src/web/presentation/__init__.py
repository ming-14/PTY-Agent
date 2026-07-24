"""展示层（Presentation Layer）。

负责处理框架相关的 HTTP / WebSocket 交互，并将请求委托给应用层。
"""

from .server import WebServer

__all__ = ["WebServer"]
