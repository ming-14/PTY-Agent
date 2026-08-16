"""Screenshare 仅查看屏幕模块。

提供 Screenshare 服务的抽象接口与具体实现：
- ScreenshareServicePort: Screenshare 服务抽象接口
- ScreenshareAdapter: Screenshare 服务实现（CaptureEngine + StreamManager）
"""

from .adapter import ScreenshareAdapter
from .ports import ScreenshareServicePort

__all__ = ["ScreenshareAdapter", "ScreenshareServicePort"]
