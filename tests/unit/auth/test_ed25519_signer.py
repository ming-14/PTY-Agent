"""Ed25519 消息签名器单元测试

覆盖：
- 构造参数校验（两者都无/都有抛异常）
- 客户端模式 sign 生成 _sig_ed25519 与 pubkey_fp
- 服务端模式 verify 验签通过
- 未授权指纹 verify 失败
- 篡改消息后验签失败
- 缺少 pubkey_fp 失败
- 模式隔离（客户端 verify 抛异常，服务端 sign 抛异常）
- verify_and_strip 剥离签名字段保留 pubkey_fp
"""

import pytest

from src.auth.keys import generate_keypair
from src.auth.pubkey import (
    Ed25519MessageSigner,
    SIG_FIELD,
    PUBKEY_FP_FIELD,
)


class TestConstructor:
    """构造参数校验"""

    def test_no_args_raises(self):
        """两者都无抛 ValueError"""
        with pytest.raises(ValueError):
            Ed25519MessageSigner()

    def test_both_args_raises(self):
        """两者都有抛 ValueError"""
        kp = generate_keypair()
        with pytest.raises(ValueError):
            Ed25519MessageSigner(
                private_key=kp, authorized_keys={kp.fingerprint: kp.public_key}
            )

    def test_name_property(self):
        """name 属性返回 ed25519"""
        kp = generate_keypair()
        signer = Ed25519MessageSigner(private_key=kp)
        assert signer.name == "ed25519"


class TestClientSign:
    """客户端模式签名"""

    def test_sign_adds_sig_and_fingerprint(self):
        """sign 添加 _sig_ed25519 与 pubkey_fp 字段"""
        kp = generate_keypair()
        signer = Ed25519MessageSigner(private_key=kp)
        msg = {"type": "exec", "cmd": "ls"}
        signed = signer.sign(msg)
        assert SIG_FIELD in signed
        assert PUBKEY_FP_FIELD in signed
        assert signed[PUBKEY_FP_FIELD] == kp.fingerprint
        # 原消息不被修改
        assert SIG_FIELD not in msg
        assert PUBKEY_FP_FIELD not in msg

    def test_sign_preserves_original_fields(self):
        """sign 保留原消息字段"""
        kp = generate_keypair()
        signer = Ed25519MessageSigner(private_key=kp)
        msg = {"type": "exec", "cmd": "ls", "id": "abc"}
        signed = signer.sign(msg)
        assert signed["type"] == "exec"
        assert signed["cmd"] == "ls"
        assert signed["id"] == "abc"

    def test_sign_is_deterministic_for_same_message(self):
        """同一消息多次签名结果一致（Ed25519 确定性签名）"""
        kp = generate_keypair()
        signer = Ed25519MessageSigner(private_key=kp)
        msg = {"type": "exec", "cmd": "ls"}
        sig1 = signer.sign(msg)[SIG_FIELD]
        sig2 = signer.sign(msg)[SIG_FIELD]
        assert sig1 == sig2

    def test_sign_server_mode_raises(self):
        """服务端模式调用 sign 抛 RuntimeError"""
        kp = generate_keypair()
        signer = Ed25519MessageSigner(authorized_keys={})
        with pytest.raises(RuntimeError):
            signer.sign({"type": "exec"})


class TestServerVerify:
    """服务端模式验签"""

    def test_verify_valid_signature(self):
        """合法签名验签通过"""
        kp = generate_keypair()
        client = Ed25519MessageSigner(private_key=kp)
        server = Ed25519MessageSigner(
            authorized_keys={kp.fingerprint: kp.public_key}
        )
        msg = {"type": "exec", "cmd": "ls", "id": "1"}
        signed = client.sign(msg)
        sig = signed.pop(SIG_FIELD)
        # signed 现在含 pubkey_fp 但不含 _sig_ed25519
        assert server.verify(signed, sig) is True

    def test_verify_unauthorized_fingerprint(self):
        """未授权指纹验签失败"""
        kp_client = generate_keypair()
        kp_other = generate_keypair()
        client = Ed25519MessageSigner(private_key=kp_client)
        # 服务端只授权了 other 公钥
        server = Ed25519MessageSigner(
            authorized_keys={kp_other.fingerprint: kp_other.public_key}
        )
        msg = {"type": "exec", "cmd": "ls"}
        signed = client.sign(msg)
        sig = signed.pop(SIG_FIELD)
        assert server.verify(signed, sig) is False

    def test_verify_tampered_message(self):
        """篡改消息内容后验签失败"""
        kp = generate_keypair()
        client = Ed25519MessageSigner(private_key=kp)
        server = Ed25519MessageSigner(
            authorized_keys={kp.fingerprint: kp.public_key}
        )
        msg = {"type": "exec", "cmd": "ls"}
        signed = client.sign(msg)
        sig = signed.pop(SIG_FIELD)
        # 篡改消息内容
        signed["cmd"] = "rm -rf /"
        assert server.verify(signed, sig) is False

    def test_verify_missing_fingerprint(self):
        """缺少 pubkey_fp 字段验签失败"""
        kp = generate_keypair()
        server = Ed25519MessageSigner(
            authorized_keys={kp.fingerprint: kp.public_key}
        )
        msg = {"type": "exec", "cmd": "ls"}  # 无 pubkey_fp
        assert server.verify(msg, "deadbeef") is False

    def test_verify_invalid_hex_signature(self):
        """签名非有效 hex 验签失败"""
        kp = generate_keypair()
        client = Ed25519MessageSigner(private_key=kp)
        server = Ed25519MessageSigner(
            authorized_keys={kp.fingerprint: kp.public_key}
        )
        msg = {"type": "exec", "cmd": "ls"}
        signed = client.sign(msg)
        signed.pop(SIG_FIELD)
        assert server.verify(signed, "not-valid-hex!") is False

    def test_verify_empty_authorized_keys(self):
        """authorized_keys 为空时所有签名失败（fail-closed）"""
        kp = generate_keypair()
        client = Ed25519MessageSigner(private_key=kp)
        server = Ed25519MessageSigner(authorized_keys={})
        msg = {"type": "exec", "cmd": "ls"}
        signed = client.sign(msg)
        sig = signed.pop(SIG_FIELD)
        assert server.verify(signed, sig) is False

    def test_verify_client_mode_raises(self):
        """客户端模式调用 verify 抛 RuntimeError"""
        kp = generate_keypair()
        signer = Ed25519MessageSigner(private_key=kp)
        with pytest.raises(RuntimeError):
            signer.verify({"type": "exec"}, "deadbeef")


class TestVerifyAndStrip:
    """verify_and_strip"""

    def test_verify_and_strip_strips_sig_keeps_fingerprint(self):
        """verify_and_strip 剥离 _sig_ed25519 但保留 pubkey_fp"""
        kp = generate_keypair()
        client = Ed25519MessageSigner(private_key=kp)
        server = Ed25519MessageSigner(
            authorized_keys={kp.fingerprint: kp.public_key}
        )
        msg = {"type": "exec", "cmd": "ls"}
        signed = client.sign(msg)
        result = server.verify_and_strip(signed)
        assert result is not None
        assert SIG_FIELD not in result
        assert PUBKEY_FP_FIELD in result  # 保留供下游认证器复用
        assert result["type"] == "exec"
        assert result["cmd"] == "ls"

    def test_verify_and_strip_missing_sig_returns_none(self):
        """缺少签名字段返回 None"""
        kp = generate_keypair()
        server = Ed25519MessageSigner(
            authorized_keys={kp.fingerprint: kp.public_key}
        )
        msg = {"type": "exec", "pubkey_fp": kp.fingerprint}  # 无 _sig_ed25519
        assert server.verify_and_strip(msg) is None

    def test_verify_and_strip_invalid_sig_returns_none(self):
        """验签失败返回 None"""
        kp = generate_keypair()
        client = Ed25519MessageSigner(private_key=kp)
        server = Ed25519MessageSigner(authorized_keys={})  # 空白名单
        msg = {"type": "exec", "cmd": "ls"}
        signed = client.sign(msg)
        assert server.verify_and_strip(signed) is None

    def test_verify_and_strip_does_not_mutate_input(self):
        """verify_and_strip 不修改输入 dict"""
        kp = generate_keypair()
        client = Ed25519MessageSigner(private_key=kp)
        server = Ed25519MessageSigner(
            authorized_keys={kp.fingerprint: kp.public_key}
        )
        msg = {"type": "exec", "cmd": "ls"}
        signed = client.sign(msg)
        original_sig = signed[SIG_FIELD]
        server.verify_and_strip(signed)
        # 输入 dict 不被修改
        assert signed[SIG_FIELD] == original_sig


class TestClientServerRoundtrip:
    """客户端签名 → 服务端验签 完整往返"""

    def test_full_roundtrip(self):
        """完整签名验签往返"""
        kp = generate_keypair()
        client = Ed25519MessageSigner(private_key=kp)
        server = Ed25519MessageSigner(
            authorized_keys={kp.fingerprint: kp.public_key}
        )
        msg = {"type": "exec", "cmd": "ls -la", "id": "test-1"}
        signed = client.sign(msg)
        result = server.verify_and_strip(signed)
        assert result is not None
        assert result["type"] == "exec"
        assert result["cmd"] == "ls -la"
        assert result["id"] == "test-1"

    def test_roundtrip_with_special_chars(self):
        """含特殊字符的消息往返"""
        kp = generate_keypair()
        client = Ed25519MessageSigner(private_key=kp)
        server = Ed25519MessageSigner(
            authorized_keys={kp.fingerprint: kp.public_key}
        )
        msg = {"type": "send", "data": "中文测试 \\n\t tab", "id": "x"}
        signed = client.sign(msg)
        result = server.verify_and_strip(signed)
        assert result is not None
        assert result["data"] == "中文测试 \\n\t tab"

    def test_roundtrip_with_nested_dict(self):
        """含嵌套 dict 的消息往返"""
        kp = generate_keypair()
        client = Ed25519MessageSigner(private_key=kp)
        server = Ed25519MessageSigner(
            authorized_keys={kp.fingerprint: kp.public_key}
        )
        msg = {"type": "config", "opts": {"a": 1, "b": [2, 3]}, "id": "y"}
        signed = client.sign(msg)
        result = server.verify_and_strip(signed)
        assert result is not None
        assert result["opts"] == {"a": 1, "b": [2, 3]}
