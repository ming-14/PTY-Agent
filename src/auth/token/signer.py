"""HMAC-SHA256 消息签名器

Token 认证方式的消息签名实现，使用对称密钥（HMAC-SHA256）
对消息进行双向签名与验证，防止同机其他进程伪造或篡改消息。
"""

import hashlib
import hmac
import json
from typing import Optional

from ...protocol.signing import MessageSigner
from ...logging import get_logger

_logger = get_logger("pty-auth")


class HmacMessageSigner(MessageSigner):
    """基于 HMAC-SHA256 的消息签名器

    使用规范 JSON 编码（sorted keys, ensure_ascii, 无尾换行）
    计算消息签名，防止同机其他进程伪造或篡改消息。
    """

    def __init__(self, key: bytes):
        self._key = key
        # 预建 HMAC 对象：每消息经 copy() 复用初始状态（避免重建 key 调度）
        self._hmac = hmac.new(self._key, digestmod=hashlib.sha256)

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

    def sign_bytes(self, obj: dict) -> bytes:
        """签名并直接产出 wire 字节：单次序列化

        规范 JSON（紧凑 + ensure_ascii + sort_keys）即签名内容；
        wire 与规范格式统一，_sig 直接拼接到规范字节尾部，免二次 json.dumps。
        """
        canonical = self._canonical_json(obj)
        sig = self._hmac_for(canonical)
        return canonical[:-1] + b',"_sig":"' + sig.encode("ascii") + b'"}\n'

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
        return self._hmac_for(canonical)

    def _verify_signature(self, obj: dict, signature: str) -> bool:
        canonical = self._canonical_json(obj)
        expected = self._hmac_for(canonical)
        return hmac.compare_digest(expected, signature)

    def _hmac_for(self, message: bytes) -> str:
        """基于预建 HMAC 初始状态对消息计算摘要（复用 key 调度结果）"""
        h = self._hmac.copy()
        h.update(message)
        return h.hexdigest()

    @staticmethod
    def _canonical_json(obj: dict) -> bytes:
        return json.dumps(
            obj, sort_keys=True, ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
