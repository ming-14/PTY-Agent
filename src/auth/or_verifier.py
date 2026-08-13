"""OR 分发验证器 — 服务端入站多认证方式任一通过

守护进程入站专用。当服务端同时支持多种认证方式（如 Token+HMAC 与 Ed25519）
时，客户端按 CLIENT_AUTH_METHOD 单选一种，请求只携带一种签名。
OrVerifier 根据消息携带的签名字段，选择对应的子验证器验签，任一通过即放行。

设计要点：
- 仅用于入站验证（recv），不参与出站签名（sign 抛异常）
- 客户端单选保证一条消息只携带一种签名，OR 分发无歧义
- signature_fields 聚合所有子验证器的字段，供 Message.recv 判断消息是否携带签名
"""

import logging
from typing import List, Optional

from ..protocol.signing import MessageSigner

_logger = logging.getLogger("pty-auth")


class OrVerifier(MessageSigner):
    """OR 分发验证器（入站专用）

    持有多个子验证器，verify_and_strip 时按消息携带的签名字段选择对应验证器，
    任一通过即返回剥离后的消息。适用于服务端同时支持多种认证方式的场景。

    Attributes:
        _verifiers: 子验证器列表（如 HmacMessageSigner, Ed25519MessageSigner(公钥)）
    """

    def __init__(self, verifiers: List[MessageSigner]):
        if not verifiers:
            raise ValueError("OrVerifier 至少需要一个子验证器")
        self._verifiers = list(verifiers)

    @property
    def name(self) -> str:
        names = "|".join(v.name for v in self._verifiers)
        return f"or({names})"

    @property
    def signature_fields(self) -> tuple:
        """聚合所有子验证器的签名字段名"""
        fields = []
        for verifier in self._verifiers:
            fields.extend(verifier.signature_fields)
        return tuple(fields)

    @property
    def verifiers(self) -> List[MessageSigner]:
        """子验证器列表（只读视图）"""
        return list(self._verifiers)

    def verify_and_strip(self, msg: dict) -> Optional[dict]:
        """OR 分发验签：按消息携带的签名字段选择验证器，任一通过即放行

        遍历子验证器，找到其签名字段存在于 msg 中的验证器进行验签。
        客户端单选保证一条消息只携带一种签名，因此匹配到的第一个验证器即负责验签。
        若该验证器验签失败，继续尝试其他验证器（兼容消息携带多种签名的边界情况）。

        Args:
            msg: 接收到的消息字典（包含签名字段）。

        Returns:
            验证通过时返回移除签名字段后的消息副本，全部失败返回 None。
        """
        msg = dict(msg)
        for verifier in self._verifiers:
            fields = verifier.signature_fields
            if not any(f in msg for f in fields):
                continue
            # 消息携带此验证器的签名字段，用它验签
            result = verifier.verify_and_strip(msg)
            if result is not None:
                return result
            _logger.warning(
                "OrVerifier: 子验证器 %s 验签失败，尝试下一个", verifier.name
            )
        _logger.warning("OrVerifier: 无子验证器匹配消息的签名字段，验签失败")
        return None

    def sign(self, obj: dict) -> dict:
        """签名（不适用）

        OrVerifier 是入站验证器，不参与出站签名。

        Raises:
            NotImplementedError: 始终抛出。
        """
        raise NotImplementedError("OrVerifier 是入站验证器，不支持 sign()")

    def verify(self, obj: dict, signature: str) -> bool:
        """单签名验证（不适用）

        OrVerifier 持有多个验证器，无法用单一 signature 参数验证。
        请使用 verify_and_strip。

        Raises:
            NotImplementedError: 始终抛出。
        """
        raise NotImplementedError("OrVerifier 请使用 verify_and_strip()")
