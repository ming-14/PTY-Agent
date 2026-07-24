"""认证子包 — 可插拔的认证与消息签名

提供认证器（Authenticator）、凭证提供者（CredentialProvider）
和消息签名器（MessageSigner）的抽象接口与具体实现。

两种认证方式独立分包：
- token/:  Token + HMAC 认证（同机，SHM 发现，对称密钥双向签名）
- pubkey/: Ed25519 公钥认证（跨机，TLS 传输，非对称签名 + 白名单验签）

共享基础设施（顶层）：
- base.py:       抽象接口（MessageSigner, Authenticator, CredentialProvider）
- keys.py:       Ed25519 密钥实体（PublicKey, PrivateKey, 生成/加载/指纹）
- context.py:    连接级认证上下文（AuthContext）
- composite.py:  组合认证器（OR 语义，任一通过即放行）
- or_verifier.py: OR 分发验证器（服务端入站多认证方式）
- tls/:          TLS 基础设施（证书管理 + TOFU 信任存储）
"""

from .base import Authenticator, CredentialProvider, MessageSigner
from .context import AuthContext
from .composite import CompositeAuthenticator
from .keys import PublicKey, PrivateKey, generate_keypair, load_authorized_keys
from .or_verifier import OrVerifier

__all__ = [
    "Authenticator",
    "CredentialProvider",
    "MessageSigner",
    "AuthContext",
    "CompositeAuthenticator",
    "PublicKey",
    "PrivateKey",
    "generate_keypair",
    "load_authorized_keys",
    "OrVerifier",
]
