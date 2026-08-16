"""Token 认证器与凭证提供者

提供基于共享密钥令牌的身份认证：

- TokenAuthenticator:       服务端令牌验证器（含轮换、宽限期）
- TokenCredentialProvider:  客户端令牌凭证提供者（从 SHM 读取令牌）
"""

import threading
import time

from ...config.daemon import AUTH_TOKEN_GRACE_PERIOD
from ..base import Authenticator, CredentialProvider
from ...logging import get_logger

_logger = get_logger("pty-auth")


class TokenAuthenticator(Authenticator):
    """基于令牌的服务端认证器

    维护一组有效令牌及其过期时间。支持令牌轮换：
    新令牌无限期有效，旧令牌在宽限期内仍可使用。

    Attributes:
        _tokens: 令牌到过期时间的映射。float("inf") 表示永不过期。
        _grace_period: 旧令牌宽限期（秒）。
    """

    def __init__(self, token: str = "", grace_period: float = AUTH_TOKEN_GRACE_PERIOD):
        self._lock = threading.Lock()
        self._grace_period = grace_period
        self._tokens: dict = {token: float("inf")} if token else {}

    @property
    def name(self) -> str:
        return "token"

    def authenticate(self, msg: dict) -> bool:
        token = msg.get("token", "")
        return self._is_token_valid(token)

    def rotate_token(self, new_token: str, old_token: str):
        """轮换令牌

        新令牌无限期有效，旧令牌给予宽限期。

        Args:
            new_token: 新生成的令牌。
            old_token: 即将过期的旧令牌。
        """
        now = time.monotonic()
        with self._lock:
            self._tokens[new_token] = float("inf")
            if old_token:
                self._tokens[old_token] = now + self._grace_period
        _logger.debug("令牌已轮换，旧令牌宽限期 %d 秒", self._grace_period)

    def _is_token_valid(self, token: str) -> bool:
        now = time.monotonic()
        with self._lock:
            deadline = self._tokens.get(token)
            if deadline is None:
                return False
            if deadline <= now:
                self._tokens.pop(token, None)
                return False
            return True


class TokenCredentialProvider(CredentialProvider):
    """基于令牌的客户端凭证提供者

    从共享内存读取守护进程发布的认证令牌，
    并附加到请求消息的 "token" 字段。
    """

    def enrich(self, msg: dict) -> dict:
        msg["token"] = self._read_token()
        return msg

    @staticmethod
    def _read_token() -> str:
        from ...ipc.shm import read_auth_token

        return read_auth_token() or ""
