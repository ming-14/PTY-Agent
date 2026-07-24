"""HMAC-SHA256 消息签名器

Token 认证方式的消息签名实现，使用对称密钥（HMAC-SHA256）
对消息进行双向签名与验证，防止同机其他进程伪造或篡改消息。
"""

import json
import hmac
import hashlib
import logging
from typing import Optional

from ..base import MessageSigner

_logger = logging.getLogger("pty-auth")


class HmacMessageSigner(MessageSigner):
    """基于 HMAC-SHA256 的消息签名器

    使用规范 JSON 编码（sorted keys, ensure_ascii, 无尾换行）
    计算消息签名，防止同机其他进程伪造或篡改消息。
    """

    def __init__(self, key: bytes):
        self._key = key

    @property
    def name(self) -> str:
        return "hmac-sha256"

    @property
    def signature_fields(self) -> tuple:
        return ("_sig",)

    def sign(self, obj: dict) -> dict:
        obj = dict(obj)
        sig = self._compute_signature(obj)
        obj["_sig"] = sig
        return obj

    def verify_and_strip(self, msg: dict) -> Optional[dict]:
        sig = msg.pop("_sig", None)
        if sig is None:
            return None
        if not self.verify(msg, sig):
            _logger.warning("HMAC 签名验证失败")
            return None
        return msg

    def verify(self, obj: dict, signature: str) -> bool:
        """验证消息签名（不修改 obj）"""
        return self._verify_signature(obj, signature)

    def _compute_signature(self, obj: dict) -> str:
        canonical = self._canonical_json(obj)
        return hmac.new(self._key, canonical, hashlib.sha256).hexdigest()

    def _verify_signature(self, obj: dict, signature: str) -> bool:
        canonical = self._canonical_json(obj)
        expected = hmac.new(self._key, canonical, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def _canonical_json(obj: dict) -> bytes:
        return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
