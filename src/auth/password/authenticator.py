"""密码认证器与凭证提供者

提供基于共享密码的身份认证：

- PasswordAuthenticator:      服务端密码验证器（常量时间比较，防时序侧信道）
- PasswordCredentialProvider: 客户端密码凭证提供者（注入 password 字段）

与 HmacMessageSigner 配合使用（basic 监听器，密码即 HMAC 密钥）：
- 客户端：PasswordCredentialProvider 注入 password → HmacMessageSigner(密码) 签名
- 服务端：HmacMessageSigner(密码) 验签 → PasswordAuthenticator 校验 password 一致

配置为空密码时不启用本组件（basic 退化为无认证，由装配方决定）。
"""

import hmac

from ..base import Authenticator, CredentialProvider
from ...logging import get_logger

_logger = get_logger("pty-auth")

# 消息中的密码字段名
PASSWORD_FIELD = "password"


class PasswordAuthenticator(Authenticator):
    """基于共享密码的服务端认证器

    校验消息中 ``password`` 字段与配置密码一致，使用
    hmac.compare_digest 常量时间比较，避免时序侧信道。

    Attributes:
        _password: 配置的共享密码（非空才启用认证）
    """

    def __init__(self, password: str):
        self._password = password

    @property
    def name(self) -> str:
        return "password"

    def authenticate(self, msg: dict) -> bool:
        """校验消息中的密码是否与配置一致

        Args:
            msg: 接收到的请求消息字典（应含 password 字段）。

        Returns:
            True 表示密码一致，False 表示缺失或不匹配。
        """
        provided = msg.get("auth", {}).get(PASSWORD_FIELD, "")
        if not provided:
            _logger.warning("密码认证失败: 消息缺少 password 字段")
            return False
        if not hmac.compare_digest(provided, self._password):
            _logger.warning("密码认证失败: 密码不匹配")
            return False
        return True


class PasswordCredentialProvider(CredentialProvider):
    """基于共享密码的客户端凭证提供者

    向消息注入 ``password`` 字段（配置的共享密码），
    供服务端 PasswordAuthenticator 校验。

    Attributes:
        _password: 配置的共享密码（非空才启用认证）
    """

    def __init__(self, password: str):
        self._password = password

    def enrich(self, msg: dict) -> dict:
        """向消息注入密码

        Args:
            msg: 待发送的请求消息字典。

        Returns:
            附加了 password 字段的消息字典（原地修改并返回）。
        """
        msg.setdefault("auth", {})[PASSWORD_FIELD] = self._password
        return msg