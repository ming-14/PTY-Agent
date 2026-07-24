"""组合认证器

将多个 Authenticator 组合为一个，支持 OR（任一通过）和 AND（全部通过）。
服务端同时支持多种认证方式时，用 CompositeAuthenticator(mode="or") 组合。
"""

import logging
from typing import List

from .base import Authenticator

_logger = logging.getLogger("pty-auth")


class CompositeAuthenticator(Authenticator):
    """组合认证器

    将多个 Authenticator 组合使用：
    - mode="or":  任一认证器通过即放行（默认，适合多种认证方式可选）
    - mode="and": 所有认证器都必须通过（适合多重安全要求）
    """

    def __init__(self, authenticators: List[Authenticator], mode: str = "or"):
        if mode not in ("or", "and"):
            raise ValueError(f"mode must be 'or' or 'and', got {mode!r}")
        self._authenticators = authenticators
        self._mode = mode

    @property
    def name(self) -> str:
        names = "+".join(a.name for a in self._authenticators)
        return f"composite({names})"

    def authenticate(self, msg: dict) -> bool:
        if not self._authenticators:
            return True
        if self._mode == "or":
            return any(a.authenticate(msg) for a in self._authenticators)
        else:
            return all(a.authenticate(msg) for a in self._authenticators)
