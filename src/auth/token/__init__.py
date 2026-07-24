"""Token + HMAC 认证方式

基于共享密钥令牌的身份认证 + HMAC-SHA256 消息签名。
适用于同机场景：SHM 自动发现 + 明文 TCP + 对称密钥双向签名。

组件：
- TokenAuthenticator:       服务端令牌验证器（含轮换、宽限期）
- TokenCredentialProvider:  客户端令牌凭证提供者（从 SHM 读取令牌）
- HmacMessageSigner:        HMAC-SHA256 消息签名器（对称，双向）
"""

from .authenticator import TokenAuthenticator, TokenCredentialProvider
from .signer import HmacMessageSigner

__all__ = [
    "TokenAuthenticator",
    "TokenCredentialProvider",
    "HmacMessageSigner",
]
