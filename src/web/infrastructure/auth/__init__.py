"""基础设施层：Web 认证适配器。

提供服务端会话存储（SessionStore），用于密码认证后的 Cookie 会话管理。
"""

from .session_store import SessionStore

__all__ = ["SessionStore"]
