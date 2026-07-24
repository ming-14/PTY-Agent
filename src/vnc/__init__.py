"""VNC 远程桌面模块。

提供 VNC 服务的抽象接口与具体实现：
- VncServicePort: VNC 服务抽象接口
- VncAdapter: VNC 服务实现（winvnc.exe）
- get_novnc_web_dir: 返回 noVNC 前端静态目录路径
"""

from .ports import VncServicePort
from .adapter import VncAdapter, get_novnc_web_dir

__all__ = ["VncServicePort", "VncAdapter", "get_novnc_web_dir"]
