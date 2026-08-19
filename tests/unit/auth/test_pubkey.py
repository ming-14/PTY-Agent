"""PubkeyAuthenticator 与 PubkeyCredentialProvider 单元测试

覆盖：
- PubkeyAuthenticator 白名单命中/未命中
- authorized_keys 为空时 fail-closed
- 缺少 pubkey_fp 字段失败
- PubkeyCredentialProvider 正确注入 pubkey_fp
- 完整客户端 enrich → 服务端 authenticate 流程
"""

from src.auth.keys import generate_keypair
from src.auth.pubkey import PubkeyAuthenticator, PubkeyCredentialProvider


class TestPubkeyAuthenticator:
    """PubkeyAuthenticator 白名单校验"""

    def test_authorized_fingerprint_passes(self):
        """授权指纹通过"""
        kp = generate_keypair()
        auth = PubkeyAuthenticator({kp.fingerprint: kp.public_key})
        msg = {"type": "exec", "auth": {"pubkey_fp": kp.fingerprint}}
        assert auth.authenticate(msg) is True

    def test_unauthorized_fingerprint_fails(self):
        """未授权指纹失败"""
        kp = generate_keypair()
        other = generate_keypair()
        auth = PubkeyAuthenticator({other.fingerprint: other.public_key})
        msg = {"type": "exec", "auth": {"pubkey_fp": kp.fingerprint}}
        assert auth.authenticate(msg) is False

    def test_empty_authorized_keys_fail_closed(self):
        """authorized_keys 为空时 fail-closed"""
        kp = generate_keypair()
        auth = PubkeyAuthenticator({})
        msg = {"type": "exec", "auth": {"pubkey_fp": kp.fingerprint}}
        assert auth.authenticate(msg) is False

    def test_missing_fingerprint_field_fails(self):
        """缺少 pubkey_fp 字段失败"""
        kp = generate_keypair()
        auth = PubkeyAuthenticator({kp.fingerprint: kp.public_key})
        msg = {"type": "exec"}  # 无 pubkey_fp
        assert auth.authenticate(msg) is False

    def test_empty_fingerprint_fails(self):
        """pubkey_fp 为空字符串失败"""
        kp = generate_keypair()
        auth = PubkeyAuthenticator({kp.fingerprint: kp.public_key})
        msg = {"type": "exec", "auth": {"pubkey_fp": ""}}
        assert auth.authenticate(msg) is False

    def test_name_property(self):
        """name 属性返回 pubkey"""
        auth = PubkeyAuthenticator({})
        assert auth.name == "pubkey"

    def test_multiple_authorized_keys(self):
        """多个授权公钥"""
        kp1 = generate_keypair()
        kp2 = generate_keypair()
        kp3 = generate_keypair()
        auth = PubkeyAuthenticator({
            kp1.fingerprint: kp1.public_key,
            kp2.fingerprint: kp2.public_key,
            kp3.fingerprint: kp3.public_key,
        })
        # 三个都通过
        assert auth.authenticate({"auth": {"pubkey_fp": kp1.fingerprint}}) is True
        assert auth.authenticate({"auth": {"pubkey_fp": kp2.fingerprint}}) is True
        assert auth.authenticate({"auth": {"pubkey_fp": kp3.fingerprint}}) is True
        # 未授权的失败
        other = generate_keypair()
        assert auth.authenticate({"auth": {"pubkey_fp": other.fingerprint}}) is False


class TestPubkeyCredentialProvider:
    """PubkeyCredentialProvider 凭证注入"""

    def test_enrich_adds_fingerprint(self):
        """enrich 注入 pubkey_fp 字段（auth 段）"""
        kp = generate_keypair()
        provider = PubkeyCredentialProvider(kp)
        msg = {"type": "exec", "cmd": "ls"}
        result = provider.enrich(msg)
        assert "pubkey_fp" in result["auth"]
        assert result["auth"]["pubkey_fp"] == kp.fingerprint

    def test_enrich_mutates_in_place(self):
        """enrich 原地修改消息"""
        kp = generate_keypair()
        provider = PubkeyCredentialProvider(kp)
        msg = {"type": "exec", "cmd": "ls"}
        result = provider.enrich(msg)
        assert result is msg  # 原地修改并返回

    def test_enrich_preserves_other_fields(self):
        """enrich 保留其他字段"""
        kp = generate_keypair()
        provider = PubkeyCredentialProvider(kp)
        msg = {"type": "exec", "cmd": "ls", "id": "abc"}
        provider.enrich(msg)
        assert msg["type"] == "exec"
        assert msg["cmd"] == "ls"
        assert msg["id"] == "abc"

    def test_fingerprint_property(self):
        """fingerprint 属性返回私钥对应的公钥指纹"""
        kp = generate_keypair()
        provider = PubkeyCredentialProvider(kp)
        assert provider.fingerprint == kp.fingerprint


class TestEndToEndAuthFlow:
    """端到端：客户端 enrich → 服务端 authenticate（tls 监听器单选语义）"""

    def test_pubkey_only_flow(self):
        """仅公私钥认证的完整流程"""
        kp = generate_keypair()
        # 客户端
        client_provider = PubkeyCredentialProvider(kp)
        # 服务端
        server_auth = PubkeyAuthenticator({kp.fingerprint: kp.public_key})
        # 客户端构造消息
        msg = {"type": "exec", "cmd": "ls"}
        client_provider.enrich(msg)
        # 服务端认证
        assert server_auth.authenticate(msg) is True