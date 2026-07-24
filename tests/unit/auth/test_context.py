"""AuthContext 单元测试

验证认证上下文的构造、属性存储、__repr__ 输出。
AuthContext 是框架层对象，封装单个 Listener 的认证配置。
"""

from src.auth.context import AuthContext
from src.auth.token import HmacMessageSigner
from src.auth.token import TokenAuthenticator


class TestAuthContextConstruction:
    """AuthContext 构造测试"""

    def test_default_all_none(self):
        """无参数构造时所有属性为 None"""
        ctx = AuthContext()
        assert ctx.outbound_signer is None
        assert ctx.inbound_verifier is None
        assert ctx.authenticator is None

    def test_with_outbound_signer(self):
        """仅设置出站签名器"""
        signer = HmacMessageSigner(b"test-key-32-bytes-need-more!!!!!")
        ctx = AuthContext(outbound_signer=signer)
        assert ctx.outbound_signer is signer
        assert ctx.inbound_verifier is None
        assert ctx.authenticator is None

    def test_with_inbound_verifier(self):
        """仅设置入站验证器"""
        verifier = HmacMessageSigner(b"test-key-32-bytes-need-more!!!!!")
        ctx = AuthContext(inbound_verifier=verifier)
        assert ctx.outbound_signer is None
        assert ctx.inbound_verifier is verifier
        assert ctx.authenticator is None

    def test_with_authenticator(self):
        """仅设置认证器"""
        auth = TokenAuthenticator("test-token")
        ctx = AuthContext(authenticator=auth)
        assert ctx.outbound_signer is None
        assert ctx.inbound_verifier is None
        assert ctx.authenticator is auth

    def test_with_all_parameters(self):
        """同时设置所有参数"""
        signer = HmacMessageSigner(b"test-key-32-bytes-need-more!!!!!")
        auth = TokenAuthenticator("test-token")
        ctx = AuthContext(
            outbound_signer=signer,
            inbound_verifier=signer,
            authenticator=auth,
        )
        assert ctx.outbound_signer is signer
        assert ctx.inbound_verifier is signer
        assert ctx.authenticator is auth


class TestAuthContextRepr:
    """AuthContext.__repr__ 测试"""

    def test_repr_empty_context(self):
        """空上下文的 repr 包含 None"""
        ctx = AuthContext()
        r = repr(ctx)
        assert "AuthContext" in r
        assert "None" in r

    def test_repr_with_signer(self):
        """带签名器的 repr 包含 signer 信息"""
        signer = HmacMessageSigner(b"test-key-32-bytes-need-more!!!!!")
        ctx = AuthContext(outbound_signer=signer)
        r = repr(ctx)
        assert "AuthContext" in r
        assert "HmacMessageSigner" in r

    def test_repr_with_authenticator(self):
        """带认证器的 repr 包含 authenticator 信息"""
        auth = TokenAuthenticator("test-token")
        ctx = AuthContext(authenticator=auth)
        r = repr(ctx)
        assert "AuthContext" in r
        assert "TokenAuthenticator" in r


class TestAuthContextHmacSymmetric:
    """HMAC 对称模式场景测试

    HMAC 对称：同一实例同时作为出站签名器和入站验证器
    """

    def test_hmac_symmetric_context(self):
        """HMAC 对称模式：同一 signer 用于签响应和验请求"""
        key = b"test-key-32-bytes-need-more!!!!!!"
        hmac_signer = HmacMessageSigner(key)
        auth = TokenAuthenticator("test-token")
        ctx = AuthContext(
            outbound_signer=hmac_signer,
            inbound_verifier=hmac_signer,
            authenticator=auth,
        )
        # 同一对象引用，验证对称性
        assert ctx.outbound_signer is ctx.inbound_verifier
        assert ctx.authenticator is auth


class TestAuthContextPubkeyAsymmetric:
    """公私钥非对称模式场景测试

    非对称单向：daemon 仅验请求（入站），不签响应（出站为 None）
    """

    def test_pubkey_asymmetric_context(self):
        """公私钥模式：出站为 None，仅入站有验证器"""
        from src.auth.pubkey import Ed25519MessageSigner
        authorized_keys = {}  # 空白名单
        verifier = Ed25519MessageSigner(authorized_keys=authorized_keys)
        ctx = AuthContext(
            outbound_signer=None,
            inbound_verifier=verifier,
            authenticator=None,
        )
        assert ctx.outbound_signer is None
        assert ctx.inbound_verifier is verifier
        assert ctx.authenticator is None
