"""认证子包 —— 可插拔认证与消息签名

提供认证器（Authenticator）、凭证提供者（CredentialProvider）的抽象接口与具体实现。
消息签名抽象（MessageSigner）属协议域（`protocol/signing.py`），
本包实现它（HmacMessageSigner / Ed25519MessageSigner）。

三种认证方式分三个子包：
- token/:    Token + HMAC 认证（同机，SHM 分发，对称密钥双向签名）
- pubkey/:   Ed25519 密钥认证（跨机 TLS 运输，非对称签名 + 白名单验签）
- password/: 共享密码认证（basic 明文监听器，密码即 HMAC 密钥，空密码退化为无认证）

共享基础设施（本包）：
- base.py:    抽象接口（Authenticator, CredentialProvider）
- keys.py:    Ed25519 密钥实体（PublicKey, PrivateKey, 生成/加载/指纹）
- context.py: 连接级认证上下文（AuthContext）
- tls/:       TLS 连接设施（证书管理 + TOFU 指纹存储）

三监听器架构下每个 Listener 独立持有单一认证方式的 AuthContext
（daemon.toml [listener] 段 basic/token/tls 与 client.toml [connection] 的
CONNECT_MODE 一一对应），本包不再提供单端口多认证组合设施。
"""

# 包级导出仅限轻量抽象（base/context）；keys.py 顶层引入 cryptography，
# 仅在 pubkey/TLS 场景才需要，由使用方经子模块按需导入，避免包导入即加载 crypto。
from .base import Authenticator, CredentialProvider
from .context import AuthContext

__all__ = [
    "AuthContext",
    "Authenticator",
    "CredentialProvider",
]
