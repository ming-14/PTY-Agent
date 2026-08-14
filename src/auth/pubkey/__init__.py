"""Ed25519 公钥认证方式

基于公钥指纹白名单的身份认证 + Ed25519 非对称消息签名。
适用于跨机场景：TLS 传输 + 配置指定 daemon 地址 + 非对称签名。

组件：
- PubkeyAuthenticator:        服务端认证器，校验 pubkey_fp 是否在 authorized_keys 白名单
- PubkeyCredentialProvider:   客户端凭证提供者，向消息注入 pubkey_fp 字段
- Ed25519MessageSigner:       Ed25519 消息签名器（非对称，单向签名 + 白名单验签）
"""

from .authenticator import PubkeyAuthenticator, PubkeyCredentialProvider
from .signer import PUBKEY_FP_FIELD, SIG_FIELD, Ed25519MessageSigner

__all__ = [
    "PUBKEY_FP_FIELD",
    "SIG_FIELD",
    "Ed25519MessageSigner",
    "PubkeyAuthenticator",
    "PubkeyCredentialProvider",
]
