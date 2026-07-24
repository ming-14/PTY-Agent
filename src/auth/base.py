"""认证抽象基类

定义三个核心抽象接口，供具体认证实现继承：

- Authenticator:        服务端认证器，验证客户端身份
- CredentialProvider:   客户端凭证提供者，向消息附加认证信息
- MessageSigner:        消息签名器，签名和验证消息完整性
"""

from abc import ABC, abstractmethod
from typing import Optional


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


class MessageSigner(ABC):
    """消息签名器 — 签名和验证消息完整性

    同时用于守护进程和客户端。发送时调用 sign() 附加签名，
    接收时调用 verify_and_strip() 验证并移除签名字段。
    """

    @abstractmethod
    def sign(self, obj: dict) -> dict:
        """签名消息，返回带签名的消息副本

        Args:
            obj: 待签名的消息字典。

        Returns:
            带签名字段的消息副本（不修改原 dict）。
        """
        ...

    @abstractmethod
    def verify(self, obj: dict, signature: str) -> bool:
        """验证消息签名

        Args:
            obj: 消息字典（不含签名字段）。
            signature: 待验证的签名字符串。

        Returns:
            True 表示签名验证通过。
        """
        ...

    @abstractmethod
    def verify_and_strip(self, msg: dict) -> Optional[dict]:
        """验证消息签名并移除签名字段

        Args:
            msg: 接收到的消息字典（包含签名字段）。

        Returns:
            验证通过时返回移除签名字段后的消息副本，失败返回 None。
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """签名器名称（用于日志）"""
        ...

    @property
    @abstractmethod
    def signature_fields(self) -> tuple:
        """该签名器写入消息的签名字段名元组

        用于接收端判断消息是否携带签名（区分"有签名需验证"与"无签名"）。
        例如 HmacMessageSigner 返回 ("_sig",)，Ed25519MessageSigner 返回 ("_sig_ed25519",)。
        OrVerifier 聚合所有子验证器的字段。
        """
        ...
