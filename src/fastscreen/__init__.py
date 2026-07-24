"""FastScreen 仅查看屏幕模块。

提供 FastScreen 服务的抽象接口与具体实现：
- FastScreenServicePort: FastScreen 服务抽象接口
- FastScreenAdapter: FastScreen 服务实现（CaptureEngine + StreamManager）
"""

from .ports import FastScreenServicePort
from .adapter import FastScreenAdapter

__all__ = ["FastScreenServicePort", "FastScreenAdapter"]
