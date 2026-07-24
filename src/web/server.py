"""WebServer 兼容性导出。

WebServer 的具体实现已迁移到展示层：
    src/web/presentation/server.py

此处保留导出以兼容现有导入路径 `from ..web.server import WebServer`。
"""

from .presentation.server import WebServer

__all__ = ["WebServer"]
