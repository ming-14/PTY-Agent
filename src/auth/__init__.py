"""认证子包 —— 可插拔认证与消息签名

提供认证器（Authenticator）、凭证提供者（CredentialProvider）的抽象接口与具体实现。
消息签名抽象（MessageSigner）属协议域（`protocol/signing.py`），
本包实现它（HmacMessageSigner / Ed25519MessageSigner）。

两种认证方式分两个子包：
- token/:  Token + HMAC 认证（同机，SHM 分发，对称密钥双向签名）
- pubkey/: Ed25519 密钥认证（跨机 TLS 运输，非对称签名 + 白名单验签）

共享基础设施（本包）：
- base.py:       抽象接口（Authenticator, CredentialProvider）
- keys.py:       Ed25519 密钥实体（PublicKey, PrivateKey, 生成/加载/指纹）
- context.py:    连接级认证上下文（AuthContext）
- composite.py:  组合认证器（OR 通过则放行）
- or_verifier.py: OR 分发验签器（服务端支持多认证方式并存）
- tls/:          TLS 连接设施（证书管理 + TOFU 指纹存储）
"""

from .base import Authenticator, CredentialProvider
from .context import AuthContext
from .composite import CompositeAuthenticator
from .keys import PublicKey, PrivateKey, generate_keypair, load_authorized_keys
from .or_verifier import OrVerifier

__all__ = [
    "Authenticator",
    "CredentialProvider",
    "AuthContext",
    "CompositeAuthenticator",
    "PublicKey",
    "PrivateKey",
    "generate_keypair",
    "load_authorized_keys",
    "OrVerifier",
]