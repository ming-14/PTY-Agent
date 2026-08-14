"""通信协议子包 — JSON 换行分隔编解码、ANSI 过滤与统一响应构造"""

from .ansi import strip_ansi
from .message import Message
from .response import Response

__all__ = ["Message", "Response", "strip_ansi"]
