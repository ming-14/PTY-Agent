"""认证抽象基类

定义两个核心抽象接口，供具体认证实现继承：

- Authenticator:        服务端认证器，验证客户端身份
- CredentialProvider:   客户端凭证提供者，向消息附加认证信息

消息签名抽象（MessageSigner）属协议域，定义在
`protocol/signing.py`，auth 包实现它（HmacMessageSigner / Ed25519MessageSigner）。
"""

from abc import ABC, abstractmethod


class Authenticator(ABC):
    """服务端认证器 — 验证客户端身份

    守护进程侧使用。每个认证器实现一种验证方式，
    由 RequestHandler 在处理请求前调用 authenticate() 判断是否放行。
    """

    @abstractmethod
    def authenticate(self, msg: dict) -> bool:
        """验证消息中的认证信息

        Args:
            msg: 接收到的请求消息字典。

        Returns:
            True 表示认证通过，False 表示认证失败。
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """认证器名称（用于日志）"""
        ...


class CredentialProvider(ABC):
    """客户端凭证提供者 — 向消息附加认证信息

    客户端侧使用。在发送请求前调用 enrich() 向消息中
    附加当前认证方式所需的凭证数据。
    """

    @abstractmethod
    def enrich(self, msg: dict) -> dict:
        """向消息中附加认证凭证

        Args:
            msg: 待发送的请求消息字典。

        Returns:
            附加了认证凭证的消息字典（可原地修改并返回）。
        """
        ...
