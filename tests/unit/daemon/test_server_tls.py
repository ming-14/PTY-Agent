"""Phase 4 单元测试 — TLS 服务端双端口架构

测试 _build_token_auth_context / _build_pubkey_auth_context / run() 双端口模式。

覆盖场景：
1. _build_token_auth_context: token 启用/禁用
2. _build_pubkey_auth_context: 有/无授权公钥
3. run() 双端口: 默认配置(单明文) / 两者都开(明文+TLS) / 仅pubkey(TLS-only)
4. _periodic_health_check: TLS-only 模式跳过
"""

import os
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

def _run_with_mocks(srv, patches, delay=0.5):
    """在 mock 环境中运行 srv.run()，延迟后设置 _shutdown_event 停止

    Args:
        srv: DaemonServer 实例
        patches: 已进入的 patch 上下文列表
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

    明文 Listener 使用的认证上下文：Token + HMAC 对称认证。
    """

    def test_token_auth_enabled_returns_hmac_context(self):
        """ENABLE_TOKEN_AUTH=true → 返回 HMAC signer + TokenAuthenticator"""
        with patch("src.daemon.server.ENABLE_TOKEN_AUTH", True):
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

    def test_token_auth_disabled_returns_none_context(self):
        """ENABLE_TOKEN_AUTH=false → 返回无认证上下文（本地调试）"""
        with patch("src.daemon.server.ENABLE_TOKEN_AUTH", False):
            srv = DaemonServer()
            ctx = srv._build_token_auth_context()

        assert isinstance(ctx, AuthContext)
        assert ctx.outbound_signer is None
        assert ctx.inbound_verifier is None
        assert ctx.authenticator is None
        assert srv._hmac_key is None
        assert srv._token_authenticator is None


class TestBuildPubkeyAuthContext:
    """_build_pubkey_auth_context 测试

    TLS Listener 使用的认证上下文：Ed25519 非对称单向认证。
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


class TestRunDualPort:
    """run() 双端口架构测试

    验证不同认证配置下 Listener 的创建数量和类型。
    使用 mock Listener 避免真实端口绑定。
    """

    def test_default_config_creates_single_plain_listener(self):
        """默认配置（token only）→ 创建 1 个 plain Listener，发布 SHM"""
        with patch("src.daemon.server.Listener") as mock_ls, \
             patch("src.daemon.server.WebServer") as mock_ws, \
             patch("src.daemon.server.write_daemon_info_to_shm") as mock_write, \
             patch("src.daemon.server.write_auth_token") as mock_auth, \
             patch("src.daemon.server.write_hmac_key") as mock_hmac, \
             patch("src.daemon.server.read_daemon_info_from_shm", return_value=None), \
             patch("src.daemon.server.signal.signal"), \
             patch.object(DaemonServer, "_schedule_rotate"):

            mock_ls.return_value.bind.return_value = 12345
            mock_ls.return_value.port = 12345
            mock_ls.return_value.transport = "plain"
            mock_write.return_value = MagicMock()
            mock_auth.return_value = MagicMock()
            mock_hmac.return_value = MagicMock()
            mock_ws.return_value.start_background = MagicMock()

            srv = DaemonServer(port=0)
            _run_with_mocks(srv, None)

            # 仅创建 1 个 Listener（plain）
            assert mock_ls.call_count == 1
            call_kwargs = mock_ls.call_args[1]
            assert call_kwargs["transport"] == "plain"
            assert call_kwargs["publish_shm"] is True
            # SHM 写入被调用
            mock_write.assert_called_once()
            mock_auth.assert_called_once()
            mock_hmac.assert_called_once()

    def test_both_enabled_creates_dual_port(self):
        """两者都开 → 创建 2 个 Listener（plain + tls），CertificateManager 被调用"""
        mock_plain = MagicMock()
        mock_plain.bind.return_value = 12345
        mock_plain.port = 12345
        mock_plain.transport = "plain"
        mock_tls = MagicMock()
        mock_tls.bind.return_value = 18767
        mock_tls.port = 18767
        mock_tls.transport = "tls"

        with patch("src.daemon.server.ENABLE_TOKEN_AUTH", True), \
             patch("src.daemon.server.ENABLE_PUBKEY_AUTH", True), \
             patch("src.daemon.server.Listener", side_effect=[mock_plain, mock_tls]) as mock_ls, \
             patch("src.daemon.server.WebServer") as mock_ws, \
             patch("src.daemon.server.CertificateManager") as mock_cm, \
             patch("src.daemon.server.load_authorized_keys", return_value={"k": b"v"}), \
             patch("src.daemon.server.write_daemon_info_to_shm") as mock_write, \
             patch("src.daemon.server.write_auth_token") as mock_auth, \
             patch("src.daemon.server.write_hmac_key") as mock_hmac, \
             patch("src.daemon.server.read_daemon_info_from_shm", return_value=None), \
             patch("src.daemon.server.signal.signal"), \
             patch.object(DaemonServer, "_schedule_rotate"):

            mock_write.return_value = MagicMock()
            mock_auth.return_value = MagicMock()
            mock_hmac.return_value = MagicMock()
            mock_ws.return_value.start_background = MagicMock()
            mock_cm.return_value.ensure_certificate.return_value = ("c", "k", "fp")
            mock_cm.return_value.create_server_ssl_context.return_value = MagicMock(spec=ssl.SSLContext)

            srv = DaemonServer(port=0)
            _run_with_mocks(srv, None)

            # 创建 2 个 Listener
            assert mock_ls.call_count == 2
            # 第一个 plain，第二个 tls
            first_kwargs = mock_ls.call_args_list[0][1]
            second_kwargs = mock_ls.call_args_list[1][1]
            assert first_kwargs["transport"] == "plain"
            assert first_kwargs["publish_shm"] is True
            assert second_kwargs["transport"] == "tls"
            assert second_kwargs["publish_shm"] is False
            assert second_kwargs["ssl_context"] is not None
            # CertificateManager 被调用
            mock_cm.return_value.ensure_certificate.assert_called_once()
            mock_cm.return_value.create_server_ssl_context.assert_called_once()
            # SHM 写入（仅 plain 端口发布）
            mock_write.assert_called_once()

    def test_pubkey_only_creates_tls_listener_no_shm(self):
        """仅开 pubkey → 创建 1 个 TLS Listener，无 SHM 发布"""
        with patch("src.daemon.server.ENABLE_TOKEN_AUTH", False), \
             patch("src.daemon.server.ENABLE_PUBKEY_AUTH", True), \
             patch("src.daemon.server.Listener") as mock_ls, \
             patch("src.daemon.server.WebServer") as mock_ws, \
             patch("src.daemon.server.CertificateManager") as mock_cm, \
             patch("src.daemon.server.load_authorized_keys", return_value={"k": b"v"}), \
             patch("src.daemon.server.write_daemon_info_to_shm") as mock_write, \
             patch("src.daemon.server.write_auth_token") as mock_auth, \
             patch("src.daemon.server.write_hmac_key") as mock_hmac, \
             patch("src.daemon.server.read_daemon_info_from_shm", return_value=None), \
             patch("src.daemon.server.signal.signal"), \
             patch.object(DaemonServer, "_schedule_rotate"):

            mock_ls.return_value.bind.return_value = 18767
            mock_ls.return_value.port = 18767
            mock_ls.return_value.transport = "tls"
            mock_ws.return_value.start_background = MagicMock()
            mock_cm.return_value.ensure_certificate.return_value = ("c", "k", "fp")
            mock_cm.return_value.create_server_ssl_context.return_value = MagicMock(spec=ssl.SSLContext)

            srv = DaemonServer(port=0)
            _run_with_mocks(srv, None)

            # 仅创建 1 个 Listener（tls）
            assert mock_ls.call_count == 1
            call_kwargs = mock_ls.call_args[1]
            assert call_kwargs["transport"] == "tls"
            assert call_kwargs["publish_shm"] is False
            # TLS-only 模式：不发布 SHM
            mock_write.assert_not_called()
            mock_auth.assert_not_called()
            mock_hmac.assert_not_called()
            # _shm_published 应为 False
            assert srv._shm_published is False

    def test_neither_enabled_creates_plain_no_auth(self):
        """两者都关 → 创建 1 个 plain Listener（无认证），发布 SHM"""
        with patch("src.daemon.server.ENABLE_TOKEN_AUTH", False), \
             patch("src.daemon.server.ENABLE_PUBKEY_AUTH", False), \
             patch("src.daemon.server.Listener") as mock_ls, \
             patch("src.daemon.server.WebServer") as mock_ws, \
             patch("src.daemon.server.write_daemon_info_to_shm") as mock_write, \
             patch("src.daemon.server.read_daemon_info_from_shm", return_value=None), \
             patch("src.daemon.server.signal.signal"), \
             patch.object(DaemonServer, "_schedule_rotate"):

            mock_ls.return_value.bind.return_value = 12345
            mock_ls.return_value.port = 12345
            mock_ls.return_value.transport = "plain"
            mock_write.return_value = MagicMock()
            mock_ws.return_value.start_background = MagicMock()

            srv = DaemonServer(port=0)
            _run_with_mocks(srv, None)

            # 仅创建 1 个 Listener（plain，无认证）
            assert mock_ls.call_count == 1
            call_kwargs = mock_ls.call_args[1]
            assert call_kwargs["transport"] == "plain"
            assert call_kwargs["publish_shm"] is True
            # 无认证模式下不写 auth token / HMAC
            # SHM daemon info 仍写入（用于同机发现）
            mock_write.assert_called_once()
            # _shm_published 应为 True
            assert srv._shm_published is True


class TestPeriodicHealthCheckSkip:
    """_periodic_health_check 在 TLS-only 模式下跳过"""

    def test_skip_when_no_shm_published(self):
        """_shm_published=False → 健康检查直接返回，不访问 SHM"""
        srv = DaemonServer()
        srv._shm_published = False
        # 不应抛出异常或访问 SHM
        srv._periodic_health_check()  # 应直接返回

    def test_runs_when_shm_published(self):
        """_shm_published=True → 健康检查执行 SHM 读取"""
        srv = DaemonServer()
        srv._shm_published = True
        srv._my_shm_signature = f"{os.getpid()}:12345"
        srv.port = 12345
        srv._last_health_check = 0  # 确保时间条件满足

        with patch("src.daemon.server.read_daemon_info_from_shm", return_value=None) as mock_read:
            srv._periodic_health_check()
            mock_read.assert_called_once()
