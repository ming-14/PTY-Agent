"""密码认证方式（basic 监听器）

基于共享密码的身份认证 + 密码即 HMAC 密钥的双向签名。
适用于 basic 明文监听器：密码为空时退化为无认证。

组件：
- PasswordAuthenticator:      服务端密码验证器（常量时间比较）
- PasswordCredentialProvider: 客户端密码凭证提供者（注入 password 字段）
- HmacMessageSigner:          密码即密钥的 HMAC 双向签名（复用 token 签名器）
"""

from .authenticator import PasswordAuthenticator, PasswordCredentialProvider

__all__ = [
    "PasswordAuthenticator",
    "PasswordCredentialProvider",
]