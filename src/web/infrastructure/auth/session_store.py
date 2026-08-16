"""服务端会话存储（基础设施层）。

管理认证会话 token 的创建、校验、撤销与过期清理。
token 由 secrets.token_hex(32) 生成，存储为 dict[token → expiry_timestamp]。
线程安全（threading.Lock），懒清理（validate 时顺带清除过期项）。
"""

from ....logging import get_logger
import secrets
import threading
import time

_logger = get_logger("pty-web-auth")

_DEFAULT_MAX_AGE = 86400  # 24h


class SessionStore:
    """服务端会话存储。

    token → expiry_timestamp 的线程安全字典。
    """

    def __init__(self):
        self._sessions: dict = {}
        self._lock = threading.Lock()

    def create(self, max_age: int = _DEFAULT_MAX_AGE) -> str:
        """创建新会话，返回 token。

        Args:
            max_age: 会话有效期（秒），默认 24h

        Returns:
            hex token 字符串
        """
        token = secrets.token_hex(32)
        expiry = time.monotonic() + max_age
        with self._lock:
            self._sessions[token] = expiry
        _logger.info(
            "session created, max_age=%d, total=%d", max_age, len(self._sessions)
        )
        return token

    def validate(self, token: str) -> bool:
        """校验 token 是否有效且未过期。

        顺带清除所有过期会话（懒清理）。

        Args:
            token: 待校验的 hex token

        Returns:
            True 表示有效
        """
        if not token:
            return False
        now = time.monotonic()
        with self._lock:
            expiry = self._sessions.get(token)
            if expiry is None:
                return False
            if now > expiry:
                del self._sessions[token]
                self._cleanup_expired_locked(now)
                _logger.info("session expired, total=%d", len(self._sessions))
                return False
            # 懒清理：顺带清除过期项
            self._cleanup_expired_locked(now)
            return True

    def revoke(self, token: str) -> None:
        """撤销指定会话。

        Args:
            token: 待撤销的 hex token
        """
        with self._lock:
            removed = self._sessions.pop(token, None)
        if removed is not None:
            _logger.info("session revoked, total=%d", len(self._sessions))

    def cleanup(self) -> None:
        """主动清除所有过期会话。"""
        now = time.monotonic()
        with self._lock:
            self._cleanup_expired_locked(now)

    def _cleanup_expired_locked(self, now: float) -> None:
        """清除过期会话（调用方已持锁）。"""
        expired = [k for k, v in self._sessions.items() if now > v]
        for k in expired:
            del self._sessions[k]
        if expired:
            _logger.debug(
                "cleaned %d expired sessions, remaining=%d",
                len(expired),
                len(self._sessions),
            )
