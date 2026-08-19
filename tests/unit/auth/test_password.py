"""PasswordAuthenticator 与 PasswordCredentialProvider 单元测试

覆盖：
- PasswordAuthenticator 密码匹配/不匹配
- 缺少 password 字段失败
- PasswordCredentialProvider 正确注入 password
- 完整客户端 enrich → 服务端 authenticate 流程
- 密码即 HMAC 密钥的签名环（HmacMessageSigner 双向）
"""

from src.auth.password import PasswordAuthenticator, PasswordCredentialProvider
from src.auth.token import HmacMessageSigner


class TestPasswordAuthenticator:
    """PasswordAuthenticator 密码校验"""

    def test_matching_password_passes(self):
        """密码一致通过"""
        auth = PasswordAuthenticator("secret")
        assert auth.authenticate({"type": "exec", "auth": {"password": "secret"}}) is True

    def test_wrong_password_fails(self):
        """密码不一致失败"""
        auth = PasswordAuthenticator("secret")
        assert auth.authenticate({"type": "exec", "auth": {"password": "wrong"}}) is False

    def test_missing_password_field_fails(self):
        """缺少 password 字段失败"""
        auth = PasswordAuthenticator("secret")
        assert auth.authenticate({"type": "exec"}) is False

    def test_empty_password_fails(self):
        """password 为空字符串失败（配置非空时）"""
        auth = PasswordAuthenticator("secret")
        assert auth.authenticate({"type": "exec", "auth": {"password": ""}}) is False

    def test_name_property(self):
        """name 属性返回 password"""
        auth = PasswordAuthenticator("secret")
        assert auth.name == "password"


class TestPasswordCredentialProvider:
    """PasswordCredentialProvider 凭证注入"""

    def test_enrich_adds_password(self):
        """enrich 注入 password 字段（auth 段）"""
        provider = PasswordCredentialProvider("secret")
        msg = {"type": "exec", "cmd": "ls"}
        result = provider.enrich(msg)
        assert result["auth"]["password"] == "secret"

    def test_enrich_mutates_in_place(self):
        """enrich 原地修改消息"""
        provider = PasswordCredentialProvider("secret")
        msg = {"type": "exec", "cmd": "ls"}
        result = provider.enrich(msg)
        assert result is msg

    def test_enrich_preserves_other_fields(self):
        """enrich 保留其他字段"""
        provider = PasswordCredentialProvider("secret")
        msg = {"type": "exec", "cmd": "ls", "id": "abc"}
        provider.enrich(msg)
        assert msg["type"] == "exec"
        assert msg["cmd"] == "ls"
        assert msg["id"] == "abc"


class TestEndToEndAuthFlow:
    """端到端：客户端 enrich + 签名 → 服务端验签 + 认证（密码即 HMAC 密钥）"""

    def test_password_hmac_flow(self):
        """密码 + HMAC 签名的完整流程"""
        password = "secret"
        # 客户端：注入密码 + HMAC 签名
        client_provider = PasswordCredentialProvider(password)
        client_signer = HmacMessageSigner(password.encode("utf-8"))
        msg = {"type": "exec", "cmd": "ls"}
        client_provider.enrich(msg)
        signed = client_signer.sign(msg)
        # 服务端：验签 + 密码认证
        server_signer = HmacMessageSigner(password.encode("utf-8"))
        verified = server_signer.verify_and_strip(dict(signed))
        assert verified is not None
        server_auth = PasswordAuthenticator(password)
        assert server_auth.authenticate(verified) is True

    def test_wrong_password_rejected(self):
        """客户端密码与服务端不一致：HMAC 密钥不同，服务端验签失败"""
        client_password = "client-secret"
        server_password = "server-secret"
        # 客户端：注入密码 + HMAC 签名（密钥即密码）
        client_provider = PasswordCredentialProvider(client_password)
        client_signer = HmacMessageSigner(client_password.encode("utf-8"))
        msg = {"type": "exec", "cmd": "ls"}
        client_provider.enrich(msg)
        signed = client_signer.sign(msg)
        # 服务端：用自己配置的密码（即密钥）验签，必然失败
        server_signer = HmacMessageSigner(server_password.encode("utf-8"))
        assert server_signer.verify_and_strip(dict(signed)) is None