"""PubkeyAuthenticator 与 PubkeyCredentialProvider 单元测试

覆盖：
- PubkeyAuthenticator 白名单命中/未命中
- authorized_keys 为空时 fail-closed
- 缺少 pubkey_fp 字段失败
- PubkeyCredentialProvider 正确注入 pubkey_fp
- CompositeAuthenticator OR / AND 模式
- 完整客户端 enrich → 服务端 authenticate 流程
"""

import pytest

from src.auth.keys import generate_keypair
from src.auth.pubkey import PubkeyAuthenticator, PubkeyCredentialProvider
from src.auth.token import TokenAuthenticator, TokenCredentialProvider
from src.auth.composite import CompositeAuthenticator


class TestPubkeyAuthenticator:
    """PubkeyAuthenticator 白名单校验"""

    def test_authorized_fingerprint_passes(self):
        """授权指纹通过"""
        kp = generate_keypair()
        auth = PubkeyAuthenticator({kp.fingerprint: kp.public_key})
        msg = {"type": "exec", "pubkey_fp": kp.fingerprint}
        assert auth.authenticate(msg) is True

    def test_unauthorized_fingerprint_fails(self):
        """未授权指纹失败"""
        kp = generate_keypair()
        other = generate_keypair()
        auth = PubkeyAuthenticator({other.fingerprint: other.public_key})
        msg = {"type": "exec", "pubkey_fp": kp.fingerprint}
        assert auth.authenticate(msg) is False

    def test_empty_authorized_keys_fail_closed(self):
        """authorized_keys 为空时 fail-closed"""
        kp = generate_keypair()
        auth = PubkeyAuthenticator({})
        msg = {"type": "exec", "pubkey_fp": kp.fingerprint}
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
        msg = {"type": "exec", "pubkey_fp": ""}
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
        assert auth.authenticate({"pubkey_fp": kp1.fingerprint}) is True
        assert auth.authenticate({"pubkey_fp": kp2.fingerprint}) is True
        assert auth.authenticate({"pubkey_fp": kp3.fingerprint}) is True
        # 未授权的失败
        other = generate_keypair()
        assert auth.authenticate({"pubkey_fp": other.fingerprint}) is False


class TestPubkeyCredentialProvider:
    """PubkeyCredentialProvider 凭证注入"""

    def test_enrich_adds_fingerprint(self):
        """enrich 注入 pubkey_fp 字段"""
        kp = generate_keypair()
        provider = PubkeyCredentialProvider(kp)
        msg = {"type": "exec", "cmd": "ls"}
        result = provider.enrich(msg)
        assert "pubkey_fp" in result
        assert result["pubkey_fp"] == kp.fingerprint

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


class TestCompositeAuthenticatorOrMode:
    """CompositeAuthenticator OR 模式（任一通过即放行）"""

    def test_both_pass_in_or_mode(self):
        """OR 模式下两者都通过"""
        kp = generate_keypair()
        token_auth = TokenAuthenticator(token="secret-token")
        pubkey_auth = PubkeyAuthenticator({kp.fingerprint: kp.public_key})
        composite = CompositeAuthenticator([token_auth, pubkey_auth], mode="or")
        msg = {"type": "exec", "token": "secret-token", "pubkey_fp": kp.fingerprint}
        assert composite.authenticate(msg) is True

    def test_only_token_passes_in_or_mode(self):
        """OR 模式下只有 token 通过也放行"""
        kp = generate_keypair()
        other = generate_keypair()
        token_auth = TokenAuthenticator(token="secret-token")
        pubkey_auth = PubkeyAuthenticator({other.fingerprint: other.public_key})
        composite = CompositeAuthenticator([token_auth, pubkey_auth], mode="or")
        msg = {"type": "exec", "token": "secret-token", "pubkey_fp": kp.fingerprint}
        assert composite.authenticate(msg) is True

    def test_only_pubkey_passes_in_or_mode(self):
        """OR 模式下只有 pubkey 通过也放行"""
        kp = generate_keypair()
        token_auth = TokenAuthenticator(token="secret-token")
        pubkey_auth = PubkeyAuthenticator({kp.fingerprint: kp.public_key})
        composite = CompositeAuthenticator([token_auth, pubkey_auth], mode="or")
        msg = {"type": "exec", "token": "wrong-token", "pubkey_fp": kp.fingerprint}
        assert composite.authenticate(msg) is True

    def test_both_fail_in_or_mode(self):
        """OR 模式下两者都失败"""
        kp = generate_keypair()
        other = generate_keypair()
        token_auth = TokenAuthenticator(token="secret-token")
        pubkey_auth = PubkeyAuthenticator({other.fingerprint: other.public_key})
        composite = CompositeAuthenticator([token_auth, pubkey_auth], mode="or")
        msg = {"type": "exec", "token": "wrong", "pubkey_fp": kp.fingerprint}
        assert composite.authenticate(msg) is False


class TestCompositeAuthenticatorAndMode:
    """CompositeAuthenticator AND 模式（全部通过才放行，用于双重安全要求）"""

    def test_both_pass_in_and_mode(self):
        """AND 模式下两者都通过"""
        kp = generate_keypair()
        token_auth = TokenAuthenticator(token="secret-token")
        pubkey_auth = PubkeyAuthenticator({kp.fingerprint: kp.public_key})
        composite = CompositeAuthenticator([token_auth, pubkey_auth], mode="and")
        msg = {"type": "exec", "token": "secret-token", "pubkey_fp": kp.fingerprint}
        assert composite.authenticate(msg) is True

    def test_only_token_passes_fails_in_and_mode(self):
        """AND 模式下只有 token 通过则失败"""
        kp = generate_keypair()
        other = generate_keypair()
        token_auth = TokenAuthenticator(token="secret-token")
        pubkey_auth = PubkeyAuthenticator({other.fingerprint: other.public_key})
        composite = CompositeAuthenticator([token_auth, pubkey_auth], mode="and")
        msg = {"type": "exec", "token": "secret-token", "pubkey_fp": kp.fingerprint}
        assert composite.authenticate(msg) is False

    def test_only_pubkey_passes_fails_in_and_mode(self):
        """AND 模式下只有 pubkey 通过则失败"""
        kp = generate_keypair()
        token_auth = TokenAuthenticator(token="secret-token")
        pubkey_auth = PubkeyAuthenticator({kp.fingerprint: kp.public_key})
        composite = CompositeAuthenticator([token_auth, pubkey_auth], mode="and")
        msg = {"type": "exec", "token": "wrong-token", "pubkey_fp": kp.fingerprint}
        assert composite.authenticate(msg) is False

    def test_both_fail_in_and_mode(self):
        """AND 模式下两者都失败"""
        kp = generate_keypair()
        other = generate_keypair()
        token_auth = TokenAuthenticator(token="secret-token")
        pubkey_auth = PubkeyAuthenticator({other.fingerprint: other.public_key})
        composite = CompositeAuthenticator([token_auth, pubkey_auth], mode="and")
        msg = {"type": "exec", "token": "wrong", "pubkey_fp": kp.fingerprint}
        assert composite.authenticate(msg) is False


class TestEndToEndAuthFlow:
    """端到端：客户端 enrich → 服务端 authenticate（OR 单选语义）"""

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

    def test_or_mode_client_token_passes(self):
        """OR 模式：服务端双开，客户端选 token → 通过"""
        kp = generate_keypair()
        token = "shared-secret-token"
        # 客户端选 token，只注入 token 凭证（单选）
        client_provider = _StubTokenProvider(token)
        # 服务端 OR 模式
        server_auth = CompositeAuthenticator([
            TokenAuthenticator(token=token),
            PubkeyAuthenticator({kp.fingerprint: kp.public_key}),
        ], mode="or")
        msg = {"type": "exec", "cmd": "ls"}
        client_provider.enrich(msg)
        assert server_auth.authenticate(msg) is True

    def test_or_mode_client_pubkey_passes(self):
        """OR 模式：服务端双开，客户端选 pubkey → 通过"""
        kp = generate_keypair()
        token = "shared-secret-token"
        # 客户端选 pubkey，只注入 pubkey_fp 凭证（单选）
        client_provider = PubkeyCredentialProvider(kp)
        # 服务端 OR 模式
        server_auth = CompositeAuthenticator([
            TokenAuthenticator(token=token),
            PubkeyAuthenticator({kp.fingerprint: kp.public_key}),
        ], mode="or")
        msg = {"type": "exec", "cmd": "ls"}
        client_provider.enrich(msg)
        assert server_auth.authenticate(msg) is True

    def test_or_mode_client_pubkey_unauthorized_rejected(self):
        """OR 模式：客户端选 pubkey 但未授权 → 拒绝（无 token 回退）"""
        kp = generate_keypair()
        other = generate_keypair()
        token = "shared-secret-token"
        # 客户端选未授权的 pubkey
        client_provider = PubkeyCredentialProvider(kp)
        # 服务端 OR 模式，但 authorized_keys 不含客户端公钥
        server_auth = CompositeAuthenticator([
            TokenAuthenticator(token=token),
            PubkeyAuthenticator({other.fingerprint: other.public_key}),
        ], mode="or")
        msg = {"type": "exec", "cmd": "ls"}
        client_provider.enrich(msg)
        # 客户端只发了 pubkey_fp，没有 token → OR 也无法通过
        assert server_auth.authenticate(msg) is False


class _StubTokenProvider:
    """测试用 Token 凭证提供者桩

    避免依赖共享内存的 TokenCredentialProvider._read_token()，
    直接注入固定 token 值。
    """

    from src.auth.base import CredentialProvider as _Base

    def __init__(self, token: str):
        self._token = token

    def enrich(self, msg: dict) -> dict:
        msg["token"] = self._token
        return msg
