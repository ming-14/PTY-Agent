"""集成测试 —— OR 语义认证组合的端到端装配验证

不启动真实 daemon，直接调用装配组件模拟客户端 enrich+sign → 服务端 verify+authenticate 流程。

注意（Phase 4 变更）：
Phase 4 双端口架构后，server.py 不再使用 OR 语义（CompositeAuthenticator + OrVerifier）
在单端口上组合多种认证。每个 Listener 独立持有单一认证方式的 AuthContext。
本测试仍验证 OR 语义组件本身的正确性（CompositeAuthenticator、OrVerifier 仍作为
可用的认证组件保留），但不再反映 server.py 的实际装配方式。

OR 语义 + 客户端单选：
- 服务端可同时支持多种认证方式（ENABLE_TOKEN_AUTH + ENABLE_PUBKEY_AUTH），任一通过即放行
- 客户端按 CLIENT_AUTH_METHOD 单选一种，请求只携带一种签名

覆盖组合：
1. 仅 token+HMAC（回归现有行为）
2. 仅 Ed25519 公私钥
3. 都开 OR，客户端选 token → 通过（即使 pubkey 未授权）
4. 都开 OR，客户端选 pubkey → 通过 / 拒绝
5. 都关（无认证）
6. 配置不一致（客户端选 token / 服务端只开 pubkey）应失败

边界场景：
- authorized_keys 为空时 fail-closed
- 未授权公钥应拒绝
- 签名篡改应失败
"""

import os
import pytest

from src.auth import (
    CompositeAuthenticator,
    OrVerifier,
    PrivateKey,
    PublicKey,
    generate_keypair,
)
from src.auth.token import (
    TokenAuthenticator,
    TokenCredentialProvider,
    HmacMessageSigner,
)
from src.auth.pubkey import (
    Ed25519MessageSigner,
    PubkeyAuthenticator,
    PubkeyCredentialProvider,
)


# ═══════════════════════════════════════════════════════════════
#  装配辅助函数（模拟 OR 语义认证组合，验证 CompositeAuthenticator + OrVerifier）
#  注意：Phase 4 后 server.py 改为双端口架构，不再使用此 OR 装配方式。
#  本函数仅用于测试 OR 语义组件本身的正确性。
# ═══════════════════════════════════════════════════════════════

def assemble_server(enable_token, enable_pubkey, auth_token, hmac_key, authorized_keys):
    """模拟 OR 语义服务端装配，返回 (outbound_signer, inbound_verifier, authenticator)

    Phase 4 前 server.py 使用此装配方式（单端口 OR 语义）：
    - 出站签名器（签响应）：仅 HMAC 可用，纯 pubkey 模式不签响应
    - 入站验证器（验请求）：多验证器用 OrVerifier OR 分发
    - 认证器：多个时用 CompositeAuthenticator(mode="or")

    Phase 4 后 server.py 改为双端口架构，每个 Listener 独立持有单一认证方式。
    本函数仅用于测试 OR 语义组件（CompositeAuthenticator、OrVerifier）的正确性。
    """
    inbound_verifiers = []
    outbound_signer = None
    authenticators = []

    if enable_token:
        hmac_signer = HmacMessageSigner(hmac_key)
        outbound_signer = hmac_signer  # 签响应
        inbound_verifiers.append(hmac_signer)  # 验请求 _sig
        authenticators.append(TokenAuthenticator(auth_token))

    if enable_pubkey:
        inbound_verifiers.append(Ed25519MessageSigner(authorized_keys=authorized_keys))
        authenticators.append(PubkeyAuthenticator(authorized_keys))

    # 入站验证器：多验证器用 OrVerifier OR 分发，单个直接用
    if len(inbound_verifiers) > 1:
        inbound_verifier = OrVerifier(inbound_verifiers)
    elif inbound_verifiers:
        inbound_verifier = inbound_verifiers[0]
    else:
        inbound_verifier = None

    # 认证器：多个时用 CompositeAuthenticator OR 组合，任一通过即放行
    authenticator = (
        CompositeAuthenticator(authenticators, mode="or")
        if len(authenticators) > 1
        else (authenticators[0] if authenticators else None)
    )
    return outbound_signer, inbound_verifier, authenticator


def assemble_client(client_auth_method, hmac_key, private_key):
    """模拟客户端装配，返回 (outbound_signer, inbound_verifier, provider)

    与 src/client/transport.py 的 _load_signer_and_providers 逻辑一致：
    - "token":  HMAC 对称，出站签请求 + 入站验响应（双向）
    - "pubkey": Ed25519 单向，出站签请求，入站不验响应
    - "none":   无认证
    """
    if client_auth_method == "token":
        signer = HmacMessageSigner(hmac_key)
        return signer, signer, TokenCredentialProvider()
    elif client_auth_method == "pubkey":
        signer = Ed25519MessageSigner(private_key=private_key)
        return signer, None, PubkeyCredentialProvider(private_key)
    else:  # none
        return None, None, None


def simulate_roundtrip(client_outbound, client_provider, server_inbound, server_authenticator, msg):
    """模拟客户端发送 → 服务端接收的完整流程（请求方向）

    Returns:
        (success: bool, msg_or_reason: dict|str)
    """
    msg = dict(msg)
    # 客户端：enrich 凭证 + sign 签名
    if client_provider is not None:
        client_provider.enrich(msg)
    if client_outbound is not None:
        msg = client_outbound.sign(msg)
    # 服务端：verify_and_strip 签名
    if server_inbound is not None:
        verified = server_inbound.verify_and_strip(msg)
        if verified is None:
            return False, "签名验证失败"
        msg = verified
    # 服务端：authenticate 身份
    if server_authenticator is not None:
        if not server_authenticator.authenticate(msg):
            return False, "认证失败"
    return True, msg


def _do_roundtrip(client_method, hmac_key, private_key,
                  enable_token, enable_pubkey, auth_token, authorized_keys, msg):
    """便捷封装：装配客户端+服务端并执行 simulate_roundtrip

    TokenCredentialProvider 从 SHM 读 token，测试环境读不到，
    用 _StubTokenProvider 直接注入固定 token 值。

    Returns:
        (success: bool, msg_or_reason: dict|str)
    """
    client_out, _, client_prov = assemble_client(client_method, hmac_key, private_key)
    _, server_in, server_auth = assemble_server(
        enable_token, enable_pubkey, auth_token, hmac_key, authorized_keys,
    )
    # TokenCredentialProvider 在测试环境无法从 SHM 读 token，用桩替换
    if isinstance(client_prov, TokenCredentialProvider):
        client_prov = _StubTokenProvider(auth_token)
    return simulate_roundtrip(client_out, client_prov, server_in, server_auth, msg)


class _StubTokenProvider:
    """测试用 Token 凭证提供者桩

    避免依赖共享内存的 TokenCredentialProvider._read_token()，
    直接注入固定 token 值。
    """

    def __init__(self, token: str):
        self._token = token

    def enrich(self, msg: dict) -> dict:
        msg["token"] = self._token
        return msg


# ═══════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def hmac_key():
    return os.urandom(32)


@pytest.fixture
def auth_token():
    return os.urandom(32).hex()


@pytest.fixture
def keypair(tmp_path):
    """生成密钥对并返回 (private_key, authorized_keys_dict)"""
    private_key = generate_keypair()
    authorized_keys = {private_key.fingerprint: private_key.public_key}
    return private_key, authorized_keys


@pytest.fixture
def empty_authorized_keys():
    return {}


# ═══════════════════════════════════════════════════════════════
#  组合 1：仅 token+HMAC
# ═══════════════════════════════════════════════════════════════

class TestTokenOnly:
    """仅启用 Token + HMAC 认证（回归现有行为）"""

    def test_valid_token_passes(self, hmac_key, auth_token):
        """合法 token + 正确 HMAC 签名 → 通过"""
        ok, result = _do_roundtrip(
            "token", hmac_key, None,
            True, False, auth_token, {},
            {"type": "exec", "id": "s1", "command": ["echo", "hi"]},
        )
        assert ok, f"合法 token 应通过: {result}"

    def test_wrong_token_rejected(self, hmac_key, auth_token):
        """错误 token → 认证失败（签名仍通过，因 HMAC 密钥一致）"""
        _, server_in, server_auth = assemble_server(
            True, False, auth_token, hmac_key, {},
        )
        client_out = HmacMessageSigner(hmac_key)
        msg = {"type": "exec", "id": "s1", "command": ["echo"]}
        msg["token"] = "wrong-token"
        msg = client_out.sign(msg)
        verified = server_in.verify_and_strip(msg)
        assert verified is not None, "HMAC 签名应通过（密钥一致）"
        assert not server_auth.authenticate(verified), "错误 token 应被拒绝"

    def test_tampered_signature_rejected(self, hmac_key, auth_token):
        """篡改签名 → 验签失败"""
        _, server_in, _ = assemble_server(
            True, False, auth_token, hmac_key, {},
        )
        client_out = HmacMessageSigner(hmac_key)
        msg = {"type": "exec", "id": "s1", "command": ["echo"]}
        msg["token"] = auth_token
        msg = client_out.sign(msg)
        msg["_sig"] = "0" * 64
        verified = server_in.verify_and_strip(msg)
        assert verified is None, "篡改的签名应验签失败"


# ═══════════════════════════════════════════════════════════════
#  组合 2：仅 Ed25519 公私钥
# ═══════════════════════════════════════════════════════════════

class TestPubkeyOnly:
    """仅启用 Ed25519 公私钥认证"""

    def test_valid_key_passes(self, hmac_key, auth_token, keypair):
        """合法私钥 + 公钥在白名单 → 通过"""
        private_key, authorized_keys = keypair
        ok, result = _do_roundtrip(
            "pubkey", hmac_key, private_key,
            False, True, auth_token, authorized_keys,
            {"type": "exec", "id": "s1", "command": ["echo"]},
        )
        assert ok, f"合法私钥应通过: {result}"
        assert "pubkey_fp" in result

    def test_unauthorized_key_rejected(self, hmac_key, auth_token, keypair):
        """私钥不在 authorized_keys → 认证失败"""
        private_key, _ = keypair
        other_key = generate_keypair()
        authorized_keys = {other_key.fingerprint: other_key.public_key}
        ok, reason = _do_roundtrip(
            "pubkey", hmac_key, private_key,
            False, True, auth_token, authorized_keys,
            {"type": "exec", "id": "s1", "command": ["echo"]},
        )
        assert not ok, "未授权公钥应被拒绝"

    def test_empty_authorized_keys_fail_closed(self, hmac_key, auth_token, keypair, empty_authorized_keys):
        """authorized_keys 为空 → fail-closed（拒绝所有）"""
        private_key, _ = keypair
        ok, reason = _do_roundtrip(
            "pubkey", hmac_key, private_key,
            False, True, auth_token, empty_authorized_keys,
            {"type": "exec", "id": "s1", "command": ["echo"]},
        )
        assert not ok, "空 authorized_keys 应 fail-closed 拒绝"

    def test_tampered_signature_rejected(self, hmac_key, auth_token, keypair):
        """篡改 Ed25519 签名 → 验签失败"""
        private_key, authorized_keys = keypair
        _, server_in, _ = assemble_server(
            False, True, auth_token, hmac_key, authorized_keys,
        )
        client_out, _, client_prov = assemble_client("pubkey", hmac_key, private_key)
        msg = {"type": "exec", "id": "s1", "command": ["echo"]}
        client_prov.enrich(msg)
        msg = client_out.sign(msg)
        msg["_sig_ed25519"] = "00" * 64
        verified = server_in.verify_and_strip(msg)
        assert verified is None, "篡改的 Ed25519 签名应验签失败"


# ═══════════════════════════════════════════════════════════════
#  组合 3：都开 OR，客户端单选
# ═══════════════════════════════════════════════════════════════

class TestBothOr:
    """Token + Ed25519 都启用，OR 语义（客户端单选其一，任一通过即放行）"""

    def test_client_token_passes(self, hmac_key, auth_token, keypair):
        """客户端选 token → HMAC 签名通过（即使 pubkey 未授权）"""
        private_key, authorized_keys = keypair
        ok, result = _do_roundtrip(
            "token", hmac_key, private_key,
            True, True, auth_token, authorized_keys,
            {"type": "exec", "id": "s1", "command": ["echo"]},
        )
        assert ok, f"OR 模式下 token 通过即放行: {result}"

    def test_client_pubkey_passes(self, hmac_key, auth_token, keypair):
        """客户端选 pubkey，合法私钥 → 通过"""
        private_key, authorized_keys = keypair
        ok, result = _do_roundtrip(
            "pubkey", hmac_key, private_key,
            True, True, auth_token, authorized_keys,
            {"type": "exec", "id": "s1", "command": ["echo"]},
        )
        assert ok, f"OR 模式下 pubkey 通过即放行: {result}"

    def test_client_pubkey_unauthorized_rejected(self, hmac_key, auth_token, keypair):
        """客户端选 pubkey，私钥未授权 → 验签失败（无 _sig 回退）"""
        private_key, _ = keypair
        other_key = generate_keypair()
        authorized_keys = {other_key.fingerprint: other_key.public_key}
        ok, reason = _do_roundtrip(
            "pubkey", hmac_key, private_key,
            True, True, auth_token, authorized_keys,
            {"type": "exec", "id": "s1", "command": ["echo"]},
        )
        assert not ok, "未授权公钥应验签失败（OR 无 _sig 回退）"

    def test_client_token_with_unauthorized_pubkey_passes(self, hmac_key, auth_token, keypair):
        """客户端选 token，pubkey 未授权 → 仍通过（OR 关键差异：客户端没发 _sig_ed25519）"""
        private_key, _ = keypair
        other_key = generate_keypair()
        authorized_keys = {other_key.fingerprint: other_key.public_key}
        ok, result = _do_roundtrip(
            "token", hmac_key, private_key,
            True, True, auth_token, authorized_keys,
            {"type": "exec", "id": "s1", "command": ["echo"]},
        )
        assert ok, f"OR: token 通过即放行，pubkey 状态无关: {result}"

    def test_or_verifier_dispatch_hmac(self, hmac_key, auth_token, keypair):
        """OrVerifier 正确分发 HMAC 签名到 HMAC 验证器"""
        _, authorized_keys = keypair
        _, server_in, _ = assemble_server(
            True, True, auth_token, hmac_key, authorized_keys,
        )
        # 消息只有 _sig（HMAC），无 _sig_ed25519
        client_hmac = HmacMessageSigner(hmac_key)
        msg = {"type": "exec", "id": "s1", "command": ["echo"]}
        msg["token"] = auth_token
        signed = client_hmac.sign(msg)
        assert "_sig" in signed
        assert "_sig_ed25519" not in signed
        verified = server_in.verify_and_strip(signed)
        assert verified is not None

    def test_or_verifier_dispatch_ed25519(self, hmac_key, auth_token, keypair):
        """OrVerifier 正确分发 Ed25519 签名到 Ed25519 验证器"""
        private_key, authorized_keys = keypair
        _, server_in, _ = assemble_server(
            True, True, auth_token, hmac_key, authorized_keys,
        )
        # 消息只有 _sig_ed25519，无 _sig
        client_ed = Ed25519MessageSigner(private_key=private_key)
        msg = {"type": "exec", "id": "s1", "command": ["echo"]}
        signed = client_ed.sign(msg)
        assert "_sig_ed25519" in signed
        assert "_sig" not in signed
        verified = server_in.verify_and_strip(signed)
        assert verified is not None


# ═══════════════════════════════════════════════════════════════
#  组合 4：都关（无认证）
# ═══════════════════════════════════════════════════════════════

class TestBothOff:
    """Token 与 Ed25519 都关闭，无认证模式"""

    def test_no_auth_passes(self, hmac_key, auth_token):
        """无认证模式 → 任意消息通过"""
        ok, result = _do_roundtrip(
            "none", hmac_key, None,
            False, False, auth_token, {},
            {"type": "exec", "id": "s1", "command": ["echo"]},
        )
        assert ok, f"无认证模式应通过: {result}"
        assert "_sig" not in result
        assert "_sig_ed25519" not in result
        assert "pubkey_fp" not in result


# ═══════════════════════════════════════════════════════════════
#  配置不一致场景
# ═══════════════════════════════════════════════════════════════

class TestConfigMismatch:
    """两端配置不一致应失败"""

    def test_client_token_server_pubkey(self, hmac_key, auth_token, keypair):
        """客户端选 token（发 _sig），服务端只开 pubkey（期望 _sig_ed25519）→ 失败"""
        private_key, authorized_keys = keypair
        ok, reason = _do_roundtrip(
            "token", hmac_key, private_key,
            False, True, auth_token, authorized_keys,
            {"type": "exec", "id": "s1", "command": ["echo"]},
        )
        # 客户端发 _sig（HMAC），服务端 OrVerifier 只有 Ed25519 验证器
        # _sig 不在 Ed25519 的 signature_fields 中 → OrVerifier 无匹配 → None
        assert not ok, "配置不一致应失败"

    def test_client_pubkey_server_token(self, hmac_key, auth_token, keypair):
        """客户端选 pubkey（发 _sig_ed25519），服务端只开 token（期望 _sig）→ 失败"""
        private_key, authorized_keys = keypair
        ok, reason = _do_roundtrip(
            "pubkey", hmac_key, private_key,
            True, False, auth_token, {},
            {"type": "exec", "id": "s1", "command": ["echo"]},
        )
        # 客户端发 _sig_ed25519，服务端只有 HMAC 验证器
        # _sig_ed25519 不在 HMAC 的 signature_fields 中 → 无匹配 → None
        assert not ok, "配置不一致应失败"
