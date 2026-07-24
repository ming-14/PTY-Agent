"""OrVerifier 单元测试 — OR 分发验证器

覆盖：
- 多验证器按签名字段分发验签（HMAC 签名 → HMAC 验证器，Ed25519 签名 → Ed25519 验证器）
- 无签名 / 篡改签名 / 未授权公钥 → 验签失败
- sign() / verify() 抛 NotImplementedError
- 空验证器列表抛 ValueError
- signature_fields 聚合、name / verifiers 属性
"""

import os
import pytest

from src.auth.or_verifier import OrVerifier
from src.auth.token import HmacMessageSigner
from src.auth.pubkey import Ed25519MessageSigner
from src.auth.keys import generate_keypair


@pytest.fixture
def hmac_key():
    return os.urandom(32)


@pytest.fixture
def keypair():
    """生成密钥对并返回 (private_key, authorized_keys_dict)"""
    private_key = generate_keypair()
    authorized_keys = {private_key.fingerprint: private_key.public_key}
    return private_key, authorized_keys


class TestOrVerifierConstruction:
    """OrVerifier 构造与属性"""

    def test_empty_verifiers_raises(self):
        """空验证器列表抛 ValueError"""
        with pytest.raises(ValueError):
            OrVerifier([])

    def test_name_property(self, hmac_key, keypair):
        """name 属性聚合子验证器名称"""
        _, authorized_keys = keypair
        hmac_v = HmacMessageSigner(hmac_key)
        ed25519_v = Ed25519MessageSigner(authorized_keys=authorized_keys)
        or_v = OrVerifier([hmac_v, ed25519_v])
        assert "hmac-sha256" in or_v.name
        assert "ed25519" in or_v.name

    def test_signature_fields_aggregation(self, hmac_key, keypair):
        """signature_fields 聚合所有子验证器的字段"""
        _, authorized_keys = keypair
        hmac_v = HmacMessageSigner(hmac_key)
        ed25519_v = Ed25519MessageSigner(authorized_keys=authorized_keys)
        or_v = OrVerifier([hmac_v, ed25519_v])
        fields = or_v.signature_fields
        assert "_sig" in fields
        assert "_sig_ed25519" in fields

    def test_verifiers_property_returns_copy(self, hmac_key):
        """verifiers 属性返回副本"""
        hmac_v = HmacMessageSigner(hmac_key)
        or_v = OrVerifier([hmac_v])
        verifiers = or_v.verifiers
        verifiers.clear()
        assert len(or_v.verifiers) == 1


class TestOrVerifierDispatch:
    """OrVerifier 按签名字段分发验签"""

    def test_hmac_signed_message_verified(self, hmac_key, keypair):
        """HMAC 签名的消息由 HMAC 验证器验签通过"""
        _, authorized_keys = keypair
        client_hmac = HmacMessageSigner(hmac_key)
        or_v = OrVerifier([
            HmacMessageSigner(hmac_key),
            Ed25519MessageSigner(authorized_keys=authorized_keys),
        ])
        msg = {"type": "exec", "command": "ls"}
        signed = client_hmac.sign(msg)
        verified = or_v.verify_and_strip(signed)
        assert verified is not None
        assert verified["type"] == "exec"
        assert "_sig" not in verified

    def test_ed25519_signed_message_verified(self, hmac_key, keypair):
        """Ed25519 签名的消息由 Ed25519 验证器验签通过"""
        private_key, authorized_keys = keypair
        client_ed25519 = Ed25519MessageSigner(private_key=private_key)
        or_v = OrVerifier([
            HmacMessageSigner(hmac_key),
            Ed25519MessageSigner(authorized_keys=authorized_keys),
        ])
        msg = {"type": "exec", "command": "ls"}
        signed = client_ed25519.sign(msg)
        verified = or_v.verify_and_strip(signed)
        assert verified is not None
        assert verified["type"] == "exec"
        assert "_sig_ed25519" not in verified

    def test_no_signature_returns_none(self, hmac_key, keypair):
        """无签名消息验签失败"""
        _, authorized_keys = keypair
        or_v = OrVerifier([
            HmacMessageSigner(hmac_key),
            Ed25519MessageSigner(authorized_keys=authorized_keys),
        ])
        msg = {"type": "exec", "command": "ls"}
        verified = or_v.verify_and_strip(msg)
        assert verified is None

    def test_tampered_hmac_signature_rejected(self, hmac_key, keypair):
        """篡改 HMAC 签名验签失败"""
        _, authorized_keys = keypair
        client_hmac = HmacMessageSigner(hmac_key)
        or_v = OrVerifier([
            HmacMessageSigner(hmac_key),
            Ed25519MessageSigner(authorized_keys=authorized_keys),
        ])
        msg = {"type": "exec", "command": "ls"}
        signed = client_hmac.sign(msg)
        signed["_sig"] = "00" * 64
        verified = or_v.verify_and_strip(signed)
        assert verified is None

    def test_tampered_ed25519_signature_rejected(self, hmac_key, keypair):
        """篡改 Ed25519 签名验签失败"""
        private_key, authorized_keys = keypair
        client_ed25519 = Ed25519MessageSigner(private_key=private_key)
        or_v = OrVerifier([
            HmacMessageSigner(hmac_key),
            Ed25519MessageSigner(authorized_keys=authorized_keys),
        ])
        msg = {"type": "exec", "command": "ls"}
        signed = client_ed25519.sign(msg)
        signed["_sig_ed25519"] = "00" * 64
        verified = or_v.verify_and_strip(signed)
        assert verified is None

    def test_single_verifier(self, hmac_key):
        """单个验证器的 OrVerifier 正常工作"""
        or_v = OrVerifier([HmacMessageSigner(hmac_key)])
        client_hmac = HmacMessageSigner(hmac_key)
        msg = {"type": "exec", "command": "ls"}
        signed = client_hmac.sign(msg)
        verified = or_v.verify_and_strip(signed)
        assert verified is not None
        assert "_sig" not in verified

    def test_unauthorized_ed25519_key_rejected(self, hmac_key):
        """Ed25519 签名但公钥不在白名单 → Ed25519 验签失败"""
        client_key = generate_keypair()
        other_key = generate_keypair()
        authorized_keys = {other_key.fingerprint: other_key.public_key}
        client_ed25519 = Ed25519MessageSigner(private_key=client_key)
        or_v = OrVerifier([
            HmacMessageSigner(hmac_key),
            Ed25519MessageSigner(authorized_keys=authorized_keys),
        ])
        msg = {"type": "exec", "command": "ls"}
        signed = client_ed25519.sign(msg)
        verified = or_v.verify_and_strip(signed)
        assert verified is None


class TestOrVerifierNotImplemented:
    """OrVerifier 不支持的方法（入站专用，不参与出站签名）"""

    def test_sign_raises(self, hmac_key):
        """sign() 抛 NotImplementedError"""
        or_v = OrVerifier([HmacMessageSigner(hmac_key)])
        with pytest.raises(NotImplementedError):
            or_v.sign({"type": "exec"})

    def test_verify_raises(self, hmac_key):
        """verify() 抛 NotImplementedError"""
        or_v = OrVerifier([HmacMessageSigner(hmac_key)])
        with pytest.raises(NotImplementedError):
            or_v.verify({"type": "exec"}, "fake_sig")
