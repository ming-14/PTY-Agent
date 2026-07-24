"""Ed25519 公钥认证器与凭证提供者

提供基于公钥指纹白名单的身份认证：

- PubkeyAuthenticator:        服务端认证器，校验消息中 pubkey_fp 是否在 authorized_keys 白名单
- PubkeyCredentialProvider:   客户端凭证提供者，向消息注入 pubkey_fp 字段

与 Ed25519MessageSigner 配合使用：
- 客户端：PubkeyCredentialProvider 注入 pubkey_fp → Ed25519MessageSigner(私钥) 签名
- 服务端：Ed25519MessageSigner(白名单) 验签 → PubkeyAuthenticator 校验 pubkey_fp 白名单

注意：Ed25519MessageSigner.verify 内部已校验 pubkey_fp 在白名单（验签需要查公钥），
PubkeyAuthenticator 是显式的身份认证层，二者职责分离但数据一致。
"""

import logging
from typing import Dict

from ..base import Authenticator, CredentialProvider
from ..keys import PrivateKey, PublicKey

_logger = logging.getLogger("pty-auth")


class PubkeyAuthenticator(Authenticator):
    """基于公钥指纹白名单的服务端认证器

    校验消息中 ``pubkey_fp`` 字段是否在 authorized_keys 白名单中。
    白名单为空时 fail-closed（所有请求拒绝）。

    Attributes:
        _authorized_keys: 指纹到 PublicKey 的映射（来自 authorized_keys 文件）
    """

    def __init__(self, authorized_keys: Dict[str, PublicKey]):
        self._authorized_keys = authorized_keys

    @property
    def name(self) -> str:
        return "pubkey"

    def authenticate(self, msg: dict) -> bool:
        """校验消息中的公钥指纹是否授权

        Args:
            msg: 接收到的请求消息字典（应含 pubkey_fp 字段）。

        Returns:
            True 表示指纹在白名单中，False 表示未授权或白名单为空。
        """
        if not self._authorized_keys:
            _logger.warning("公钥认证失败: authorized_keys 为空（fail-closed）")
            return False
        fp = msg.get("pubkey_fp", "")
        if not fp:
            _logger.warning("公钥认证失败: 消息缺少 pubkey_fp 字段")
            return False
        if fp not in self._authorized_keys:
            _logger.warning("公钥认证失败: 指纹未授权: %s...", fp[:16])
            return False
        return True


class PubkeyCredentialProvider(CredentialProvider):
    """基于公私钥的客户端凭证提供者

    向消息注入 ``pubkey_fp`` 字段（私钥对应的公钥指纹），
    供服务端 PubkeyAuthenticator 白名单校验。

    Attributes:
        _private_key: 客户端私钥（用于派生公钥指纹）
    """

    def __init__(self, private_key: PrivateKey):
        self._private_key = private_key

    @property
    def fingerprint(self) -> str:
        """当前私钥对应的公钥指纹"""
        return self._private_key.fingerprint

    def enrich(self, msg: dict) -> dict:
        """向消息注入公钥指纹

        Args:
            msg: 待发送的请求消息字典。

        Returns:
            附加了 pubkey_fp 字段的消息字典（原地修改并返回）。
        """
        msg["pubkey_fp"] = self._private_key.fingerprint
        return msg
