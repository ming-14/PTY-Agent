"""认证上下文 — 封装单个 Listener 的认证配置

框架层对象，将出站签名器、入站验证器、认证器绑定为一个单元，
供 Listener 和 Dispatcher 使用。每个 Listener 持有一个 AuthContext，
描述该端口的认证方式。

依赖规则：框架层对象，封装传输层认证配置，不包含业务逻辑。
"""

from typing import Optional

from .base import Authenticator, MessageSigner


class AuthContext:
    """连接级认证上下文

    每个 Listener 持有一个 AuthContext，描述该端口的认证方式。
    Dispatcher.handle() 在连接线程启动时将签名器设置到线程局部存储。

    Attributes:
        outbound_signer: 出站签名器（签响应），None 表示不签名。
        inbound_verifier: 入站验证器（验请求），None 表示不验签。
        authenticator: 认证器（验身份），None 表示不认证。
    """

    def __init__(
        self,
        outbound_signer: Optional[MessageSigner] = None,
        inbound_verifier: Optional[MessageSigner] = None,
        authenticator: Optional[Authenticator] = None,
    ):
        self.outbound_signer = outbound_signer
        self.inbound_verifier = inbound_verifier
        self.authenticator = authenticator

    def __repr__(self) -> str:
        return (
            f"AuthContext(outbound={self.outbound_signer}, "
            f"inbound={self.inbound_verifier}, "
            f"authenticator={self.authenticator})"
        )
