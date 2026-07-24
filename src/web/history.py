"""历史会话存储兼容性导出。

HistoryStore 的具体实现已迁移到基础设施层：
    src/web/infrastructure/repositories/history_store.py

此处保留导出以兼容现有导入路径。
"""

from .infrastructure.repositories.history_store import HistoryStore, base64_encode

__all__ = ["HistoryStore", "base64_encode"]
