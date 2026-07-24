"""Ed25519 消息签名器

Pubkey 认证方式的消息签名实现，基于 Ed25519 非对称签名。

双模式设计：
- 客户端模式：持有私钥，sign() 用私钥签名，verify() 不使用
- 服务端模式：持有 指纹->PublicKey 映射，verify() 按 msg["pubkey_fp"] 查公钥验签

签名字段: ``_sig_ed25519``（hex 编码），与 Token 方式的 ``_sig`` 字段共存。
签名内容: 消息规范 JSON（与 HmacMessageSigner._canonical_json 一致），
          额外排除 ``pubkey_fp`` 字段（指纹是身份标识，不应纳入签名内容）。
"""

import json
import logging
from typing import Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..base import MessageSigner
from ..keys import PrivateKey, PublicKey

_logger = logging.getLogger("pty-auth")

# Ed25519 签名字段名（与 HMAC 的 _sig 区分，二者可共存）
SIG_FIELD = "_sig_ed25519"

# 消息中的公钥指纹字段名
PUBKEY_FP_FIELD = "pubkey_fp"

# 签名时需排除的字段（签名字段本身 + 指纹字段）
# 指纹是身份标识，验签时用于查公钥，不应纳入签名内容
_EXCLUDED_FIELDS = (SIG_FIELD, PUBKEY_FP_FIELD)


class Ed25519MessageSigner(MessageSigner):
    """Ed25519 消息签名器

    客户端与服务端使用同一类，通过构造参数区分模式：

    - 客户端模式: ``Ed25519MessageSigner(private_key=PrivateKey(...))``
                  sign() 用私钥签名，verify() 抛 NotImplementedError
    - 服务端模式: ``Ed25519MessageSigner(authorized_keys={fp: PublicKey, ...})``
                  verify() 按 msg["pubkey_fp"] 查公钥验签，sign() 抛 NotImplementedError

    双模式设计避免客户端持有服务端的公钥集合，也避免服务端持有客户端私钥。

    Attributes:
        _private_key: 客户端模式下的私钥（服务端模式为 None）
        _authorized_keys: 服务端模式下的 指纹->PublicKey 映射（客户端模式为 None）
    """

    def __init__(
        self,
        private_key: Optional[PrivateKey] = None,
        authorized_keys: Optional[Dict[str, PublicKey]] = None,
    ):
        if private_key is None and authorized_keys is None:
            raise ValueError("必须提供 private_key 或 authorized_keys 之一")
        if private_key is not None and authorized_keys is not None:
            raise ValueError("private_key 与 authorized_keys 不能同时提供")
        self._private_key = private_key
        self._authorized_keys = authorized_keys

    @property
    def name(self) -> str:
        return "ed25519"

    @property
    def signature_fields(self) -> tuple:
        return (SIG_FIELD,)

    def sign(self, obj: dict) -> dict:
        """用私钥签名消息

        客户端模式专用。签名前注入 ``pubkey_fp`` 字段（私钥对应的公钥指纹），
        然后对排除签名字段与指纹字段后的规范 JSON 签名。

        Args:
            obj: 待签名的消息字典。

        Returns:
            带 ``pubkey_fp`` 与 ``_sig_ed25519`` 字段的消息副本。

        Raises:
            RuntimeError: 服务端模式调用 sign()。
        """
        if self._private_key is None:
            raise RuntimeError("服务端模式不支持 sign()，请用客户端模式构造")
        obj = dict(obj)
        # 注入公钥指纹，服务端据此查公钥验签
        obj[PUBKEY_FP_FIELD] = self._private_key.fingerprint
        sig = self._compute_signature(obj)
        obj[SIG_FIELD] = sig
        return obj

    def verify(self, obj: dict, signature: str) -> bool:
        """验证消息签名

        服务端模式专用。按 ``obj["pubkey_fp"]`` 查 authorized_keys 公钥验签。

        Args:
            obj: 消息字典（不含签名字段，但应含 pubkey_fp 字段）。
            signature: 待验证的签名字符串（hex）。

        Returns:
            True 表示签名验证通过（公钥在白名单且签名匹配）。

        Raises:
            RuntimeError: 客户端模式调用 verify()。
        """
        if self._authorized_keys is None:
            raise RuntimeError("客户端模式不支持 verify()，请用服务端模式构造")
        fp = obj.get(PUBKEY_FP_FIELD, "")
        if not fp:
            _logger.warning("Ed25519 验签失败: 消息缺少 pubkey_fp 字段")
            return False
        pub = self._authorized_keys.get(fp)
        if pub is None:
            _logger.warning("Ed25519 验签失败: 公钥指纹未授权: %s...", fp[:16])
            return False
        try:
            sig_bytes = bytes.fromhex(signature)
        except ValueError:
            _logger.warning("Ed25519 验签失败: 签名非有效 hex")
            return False
        canonical = self._canonical_json(obj)
        try:
            pub.key.verify(sig_bytes, canonical)
            return True
        except InvalidSignature:
            _logger.warning("Ed25519 验签失败: 签名不匹配 (fp=%s...)", fp[:16])
            return False

    def verify_and_strip(self, msg: dict) -> Optional[dict]:
        """验证签名并移除签名字段

        服务端模式专用。从 msg 中 pop ``_sig_ed25519``，校验通过后返回
        移除签名字段的消息副本（保留 pubkey_fp 供下游 PubkeyAuthenticator 复用）。

        Args:
            msg: 接收到的消息字典（包含签名字段）。

        Returns:
            验证通过时返回移除签名字段后的消息副本（保留 pubkey_fp），失败返回 None。
        """
        msg = dict(msg)
        sig = msg.pop(SIG_FIELD, None)
        if sig is None:
            _logger.warning("Ed25519 验签失败: 消息缺少 %s 字段", SIG_FIELD)
            return None
        if not self.verify(msg, sig):
            return None
        return msg

    def _compute_signature(self, obj: dict) -> str:
        """计算 Ed25519 签名

        Args:
            obj: 包含 pubkey_fp 但不含签名字段的消息字典。

        Returns:
            hex 编码的签名串。
        """
        canonical = self._canonical_json(obj)
        sig = self._private_key.key.sign(canonical)
        return sig.hex()

    @staticmethod
    def _canonical_json(obj: dict) -> bytes:
        """规范 JSON 编码

        与 HmacMessageSigner._canonical_json 一致（sort_keys + ensure_ascii + 紧凑），
        但排除签名字段与指纹字段（指纹是身份标识，不纳入签名内容）。

        Args:
            obj: 消息字典（可能含签名字段与指纹字段）。

        Returns:
            UTF-8 编码的规范 JSON 字节串。
        """
        filtered = {k: v for k, v in obj.items() if k not in _EXCLUDED_FIELDS}
        return json.dumps(
            filtered, sort_keys=True, ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
