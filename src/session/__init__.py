"""会话管理子包 — 会话生命周期、输出缓冲、触发匹配与屏幕快照"""

from .manager import SessionManager
from .session import Session

__all__ = ["Session", "SessionManager"]
