"""服务端三监听器架构单元测试

测试 _build_token_auth_context / _build_plain_auth_context / _build_pubkey_auth_context
与 run() 三种监听器的独立启停（plain / token / tls）。

覆盖场景：
1. _build_token_auth_context: Token + HMAC 对称认证上下文
2. _build_plain_auth_context: 无认证上下文
3. _build_pubkey_auth_context: 有/无授权公钥
4. run() 三监听器: 仅 token / 仅 tls / 仅 plain / 多监听器同开
"""

import ssl
import time
import threading
import pytest
from unittest.mock import patch, MagicMock

from src.daemon.server import DaemonServer
from src.auth.context import AuthContext
from src.auth.token import TokenAuthenticator
from src.auth.token import HmacMessageSigner
from src.auth.pubkey import Ed25519MessageSigner
from src.auth.pubkey import PubkeyAuthenticator


# ═══════════════════════════════════════════════════════════════
#  辅助：run() 测试的通用 mock 上下文
# ═══════════════════════════════════════════════════════════════

def _run_with_mocks(srv, delay=0.5):
    """运行 srv.run()，延迟后设置 _shutdown_event 停止

    Args:
        srv: DaemonServer 实例
        delay: 停止前等待秒数
    """
    def stop_after(srv_ref):
        time.sleep(delay)
        srv_ref._shutdown_event.set()
    t = threading.Thread(target=stop_after, args=(srv,), daemon=True)
    t.start()
    try:
        srv.run()
    except Exception:
        pass


class TestBuildTokenAuthContext:
    """_build_token_auth_context 测试

    token Listener 使用的认证上下文：Token + HMAC 对称认证。
    """

    def test_returns_hmac_context(self):
        """返回 HMAC signer + TokenAuthenticator"""
        srv = DaemonServer()
        ctx = srv._build_token_auth_context()

        assert isinstance(ctx, AuthContext)
        assert ctx.outbound_signer is not None
        assert isinstance(ctx.outbound_signer, HmacMessageSigner)
        # HMAC 对称：出站签名器 == 入站验证器（同一实例）
        assert ctx.inbound_verifier is ctx.outbound_signer
        assert isinstance(ctx.authenticator, TokenAuthenticator)
        # 副作用：hmac_key 和 token_authenticator 已保存
        assert srv._hmac_key is not None
        assert len(srv._hmac_key) == 32
        assert srv._token_authenticator is not None


class TestBuildPlainAuthContext:
    """_build_plain_auth_context 测试

    plain Listener 使用的认证上下文：无认证。
    """

    def test_returns_no_auth_context(self):
        srv = DaemonServer()
        ctx = srv._build_plain_auth_context()

        assert isinstance(ctx, AuthContext)
        assert ctx.outbound_signer is None
        assert ctx.inbound_verifier is None
        assert ctx.authenticator is None
        assert srv._hmac_key is None
        assert srv._token_authenticator is None


class TestBuildPubkeyAuthContext:
    """_build_pubkey_auth_context 测试

    tls Listener 使用的认证上下文：Ed25519 非对称单向认证。
    """

    def test_with_authorized_keys_returns_ed25519_context(self):
        """有授权公钥 → 返回 Ed25519 verifier + PubkeyAuthenticator"""
        mock_keys = {"key_id": b"fake_public_key_bytes"}
        with patch("src.daemon.server.load_authorized_keys", return_value=mock_keys):
            srv = DaemonServer()
            ctx = srv._build_pubkey_auth_context()

        assert isinstance(ctx, AuthContext)
        # pubkey 单向：daemon 不签响应（无私钥）
        assert ctx.outbound_signer is None
        assert isinstance(ctx.inbound_verifier, Ed25519MessageSigner)
        assert isinstance(ctx.authenticator, PubkeyAuthenticator)

    def test_empty_authorized_keys_still_returns_context(self):
        """authorized_keys 为空 → 仍返回上下文（fail-closed 由认证器处理）"""
        with patch("src.daemon.server.load_authorized_keys", return_value={}):
            srv = DaemonServer()
            ctx = srv._build_pubkey_auth_context()

        assert isinstance(ctx, AuthContext)
        assert ctx.outbound_signer is None
        assert isinstance(ctx.inbound_verifier, Ed25519MessageSigner)
        assert isinstance(ctx.authenticator, PubkeyAuthenticator)


class TestRunListeners:
    """run() 三监听器架构测试

    通过改写 srv.listeners_config 验证不同 enabled 组合下 Listener 的创建。
    使用 mock Listener 避免真实端口绑定。
    """

    @pytest.fixture
    def mock_env(self):
        with patch("src.daemon.server.Listener") as mock_ls, \
             patch("src.daemon.server.WebServer") as mock_ws, \
             patch("src.daemon.server.write_auth_token") as mock_auth, \
             patch("src.daemon.server.write_hmac_key") as mock_hmac, \
             patch("src.daemon.server.CertificateManager") as mock_cm, \
             patch("src.daemon.server.load_authorized_keys", return_value={"k": b"v"}), \
             patch("src.daemon.server.signal.signal"), \
             patch.object(DaemonServer, "_schedule_rotate"):
            mock_ws.return_value.start_background = MagicMock()
            mock_auth.return_value = MagicMock()
            mock_hmac.return_value = MagicMock()
            mock_cm.return_value.ensure_certificate.return_value = ("c", "k", "fp")
            mock_cm.return_value.create_server_ssl_context.return_value = MagicMock(spec=ssl.SSLContext)
            yield mock_ls, mock_ws, mock_auth, mock_hmac, mock_cm

    def _set_listeners(self, srv, playout):
        srv.listeners_config = {
            name: (enabled, host, port)
            for name, (enabled, host, port) in playout.items()
        }

    def test_token_only(self, mock_env):
        """仅 token → 创建 1 个 plain Listener，SHM 凭据发布"""
        mock_ls, mock_ws, mock_auth, mock_hmac, mock_cm = mock_env
        srv = DaemonServer()
        self._set_listeners(srv, {
            "plain": (False, "0.0.0.0", 10521),
            "token": (True, "127.0.0.1", 10520),
            "tls": (False, "0.0.0.0", 18767),
        })
        _run_with_mocks(srv)

        assert mock_ls.call_count == 1
        assert mock_ls.call_args[1]["transport"] == "plain"
        assert mock_ls.call_args[1]["host"] == "127.0.0.1"
        assert mock_ls.call_args[1]["port"] == 10520
        mock_auth.assert_called_once()
        mock_hmac.assert_called_once()

    def test_plain_only(self, mock_env):
        """仅 plain → 创建 1 个 plain Listener（无认证），无 SHM 凭据发布"""
        mock_ls, mock_ws, mock_auth, mock_hmac, mock_cm = mock_env
        srv = DaemonServer()
        self._set_listeners(srv, {
            "plain": (True, "0.0.0.0", 10521),
            "token": (False, "127.0.0.1", 10520),
            "tls": (False, "0.0.0.0", 18767),
        })
        _run_with_mocks(srv)

        assert mock_ls.call_count == 1
        assert mock_ls.call_args[1]["transport"] == "plain"
        assert mock_ls.call_args[1]["host"] == "0.0.0.0"
        assert mock_ls.call_args[1]["port"] == 10521
        mock_auth.assert_not_called()
        mock_hmac.assert_not_called()

    def test_tls_only(self, mock_env):
        """仅 tls → 创建 1 个 TLS Listener，无 SHM 凭据发布"""
        mock_ls, mock_ws, mock_auth, mock_hmac, mock_cm = mock_env
        srv = DaemonServer()
        self._set_listeners(srv, {
            "plain": (False, "0.0.0.0", 10521),
            "token": (False, "127.0.0.1", 10520),
            "tls": (True, "0.0.0.0", 18767),
        })
        _run_with_mocks(srv)

        assert mock_ls.call_count == 1
        call_kwargs = mock_ls.call_args[1]
        assert call_kwargs["transport"] == "tls"
        assert call_kwargs["ssl_context"] is not None
        mock_cm.return_value.ensure_certificate.assert_called_once()
        mock_auth.assert_not_called()
        mock_hmac.assert_not_called()

    def test_plain_plus_token(self, mock_env):
        """plain + token 同开 → 创建 2 个 plain Listener，SHM 凭据发布"""
        mock_ls, mock_ws, mock_auth, mock_hmac, mock_cm = mock_env
        srv = DaemonServer()
        self._set_listeners(srv, {
            "plain": (True, "0.0.0.0", 10521),
            "token": (True, "127.0.0.1", 10520),
            "tls": (False, "0.0.0.0", 18767),
        })
        _run_with_mocks(srv)

        assert mock_ls.call_count == 2
        first = mock_ls.call_args_list[0][1]
        second = mock_ls.call_args_list[1][1]
        assert (first["transport"], first["host"], first["port"]) == ("plain", "0.0.0.0", 10521)
        assert (second["transport"], second["host"], second["port"]) == ("plain", "127.0.0.1", 10520)
        mock_auth.assert_called_once()
        mock_hmac.assert_called_once()

    def test_token_plus_tls(self, mock_env):
        """token + tls 同开 → 创建 1 plain + 1 tls Listener"""
        mock_ls, mock_ws, mock_auth, mock_hmac, mock_cm = mock_env
        srv = DaemonServer()
        self._set_listeners(srv, {
            "plain": (False, "0.0.0.0", 10521),
            "token": (True, "127.0.0.1", 10520),
            "tls": (True, "0.0.0.0", 18767),
        })
        _run_with_mocks(srv)

        assert mock_ls.call_count == 2
        first = mock_ls.call_args_list[0][1]
        second = mock_ls.call_args_list[1][1]
        assert (first["transport"], first["host"]) == ("plain", "127.0.0.1")
        assert (second["transport"], second["host"]) == ("tls", "0.0.0.0")
        assert second["ssl_context"] is not None
        mock_auth.assert_called_once()

    def test_all_enabled(self, mock_env):
        """三监听器同开 → 创建 3 个 Listener（plain/token/tls）"""
        mock_ls, mock_ws, mock_auth, mock_hmac, mock_cm = mock_env
        srv = DaemonServer()
        self._set_listeners(srv, {
            "plain": (True, "0.0.0.0", 10521),
            "token": (True, "127.0.0.1", 10520),
            "tls": (True, "0.0.0.0", 18767),
        })
        _run_with_mocks(srv)

        assert mock_ls.call_count == 3
        transports = [c[1]["transport"] for c in mock_ls.call_args_list]
        assert transports == ["plain", "plain", "tls"]
        mock_auth.assert_called_once()
        mock_hmac.assert_called_once()

    def test_none_enabled(self, mock_env):
        """全部关闭 → 不创建 Listener，无 SHM 凭据发布"""
        mock_ls, mock_ws, mock_auth, mock_hmac, mock_cm = mock_env
        srv = DaemonServer()
        self._set_listeners(srv, {
            "plain": (False, "0.0.0.0", 10521),
            "token": (False, "127.0.0.1", 10520),
            "tls": (False, "0.0.0.0", 18767),
        })
        _run_with_mocks(srv)

        assert mock_ls.call_count == 0
        mock_auth.assert_not_called()
        mock_hmac.assert_not_called()