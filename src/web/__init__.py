"""PTY-Agent Web 模块。

内部按干净架构组织为 domain / application / infrastructure / presentation 四层。

注意：本 __init__.py 刻意不执行任何子模块导入。
原因：FastScreenAdapter 会将 src/ 加入 sys.path 并以顶级包形式导入
`web.streamers.manager`，此时 `web` 是顶级包，本文件中任何相对导入
（如 `from .history import`）都会以 `web` 为基准解析；但深层模块
（如 application/handlers.py 的 `from ...config import`）会超出顶级包
边界触发 ImportError。因此这里保持为空，所有使用方直接从子模块导入：
  - WebServer     → from .web.presentation.server import WebServer
                    （或兼容 shim：from .web.server import WebServer）
  - HistoryStore  → from .web.history import HistoryStore
                    （或底层：from .web.infrastructure.repositories.history_store import HistoryStore）
"""
