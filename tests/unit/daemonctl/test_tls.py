"""TLSClient 单元测试 — TLS 连接 + TOFU 证书验证

测试 TLSClient.connect() 的 TOFU 信任模型逻辑：
- 首次连接自动信任
- 后续连接指纹匹配
- 指纹不匹配严格模式拒绝
- 指纹不匹配非严格模式警告但继续
- 服务端未提供证书拒绝

通过 mock ssl.SSLContext / socket.socket / KnownHosts 隔离网络与文件系统。
"""

import ssl
import hashlib
from unittest.mock import MagicMock, patch

import pytest

from src.daemonctl.tls import TLSClient
from src.auth.tls.known_hosts import KnownHosts


# 测试用 DER 证书字节（任意非空字节串，用于计算指纹）
_TEST_DER_CERT = b"\x30\x82\x01\x00" + b"\x00" * 256
_TEST_FINGERPRINT = "sha256:" + hashlib.sha256(_TEST_DER_CERT).hexdigest()


def _make_mock_ssl_sock(der_cert: bytes = _TEST_DER_CERT):
    """创建 mock SSLSocket，getpeercert 返回指定 DER 证书

    不使用 spec=ssl.SSLSocket（C 类型 spec 限制过严），
    用 plain MagicMock 确保 close/getpeercert 可自由 mock。
    """
    mock_sock = MagicMock()
    mock_sock.getpeercert.return_value = der_cert
    return mock_sock


def _make_mock_known_hosts(verify_result: bool, existing_fp: str = None):
    """创建 mock KnownHosts"""
    kh = MagicMock(spec=KnownHosts)
    kh.verify.return_value = verify_result
    kh.get.return_value = existing_fp
    return kh


def _setup_tls_mocks(mock_ctx_cls, mock_sock_cls, der_cert=_TEST_DER_CERT):
    """统一设置 TLS mock 环境

    返回 (mock_ctx, mock_raw_sock, mock_ssl_sock) 三元组。
    """
    # plain MagicMock（不用 spec，避免 C 类型 SSLContext 限制）
    mock_ctx = MagicMock()
    mock_ctx_cls.return_value = mock_ctx
    mock_raw_sock = MagicMock()
    mock_sock_cls.return_value = mock_raw_sock
    mock_ssl_sock = _make_mock_ssl_sock(der_cert)
    mock_ctx.wrap_socket.return_value = mock_ssl_sock
    return mock_ctx, mock_raw_sock, mock_ssl_sock


class TestTLSClientInit:
    """TLSClient 构造函数测试"""

    def test_init_stores_params(self):
        kh = MagicMock(spec=KnownHosts)
        client = TLSClient("192.168.1.100", 18767, kh, tofu_strict=True, timeout=5.0)
        assert client.host == "192.168.1.100"
        assert client.port == 18767
        assert client.known_hosts is kh
        assert client.tofu_strict is True
        assert client.timeout == 5.0

    def test_init_defaults(self):
        kh = MagicMock(spec=KnownHosts)
        client = TLSClient("localhost", 443, kh)
        assert client.tofu_strict is True
        assert client.timeout == 10.0


class TestTLSClientConnectTofuFirstTrust:
    """TOFU 首次连接：自动信任"""

    @patch("src.daemonctl.tls.socket.socket")
    @patch("src.daemonctl.tls.ssl.SSLContext")
    def test_first_connection_auto_trusts(self, mock_ctx_cls, mock_sock_cls):
        """首次连接：KnownHosts.verify 返回 True（自动信任），返回 SSLSocket"""
        mock_ctx, mock_raw_sock, mock_ssl_sock = _setup_tls_mocks(
            mock_ctx_cls, mock_sock_cls
        )

        kh = _make_mock_known_hosts(verify_result=True)
        client = TLSClient("10.0.0.1", 18767, kh)

        result = client.connect()

        assert result is mock_ssl_sock
        kh.verify.assert_called_once_with("10.0.0.1", 18767, _TEST_FINGERPRINT)
        mock_raw_sock.connect.assert_called_once_with(("10.0.0.1", 18767))


class TestTLSClientConnectFingerprintMatch:
    """TOFU 后续连接：指纹匹配"""

    @patch("src.daemonctl.tls.socket.socket")
    @patch("src.daemonctl.tls.ssl.SSLContext")
    def test_matching_fingerprint_passes(self, mock_ctx_cls, mock_sock_cls):
        """后续连接：指纹匹配，verify 返回 True，返回 SSLSocket"""
        mock_ctx, mock_raw_sock, mock_ssl_sock = _setup_tls_mocks(
            mock_ctx_cls, mock_sock_cls
        )

        kh = _make_mock_known_hosts(
            verify_result=True,
            existing_fp=_TEST_FINGERPRINT,
        )
        client = TLSClient("10.0.0.1", 18767, kh)

        result = client.connect()

        assert result is mock_ssl_sock


class TestTLSClientConnectFingerprintMismatch:
    """TOFU 指纹不匹配"""

    @patch("src.daemonctl.tls.socket.socket")
    @patch("src.daemonctl.tls.ssl.SSLContext")
    def test_strict_mode_rejects(self, mock_ctx_cls, mock_sock_cls):
        """严格模式：指纹不匹配 → ConnectionError，关闭 socket"""
        mock_ctx, mock_raw_sock, mock_ssl_sock = _setup_tls_mocks(
            mock_ctx_cls, mock_sock_cls
        )

        kh = _make_mock_known_hosts(
            verify_result=False,
            existing_fp="sha256:aaa111",
        )
        client = TLSClient("10.0.0.1", 18767, kh, tofu_strict=True)

        with pytest.raises(ConnectionError, match="证书指纹不匹配"):
            client.connect()

        mock_ssl_sock.close.assert_called_once()

    @patch("src.daemonctl.tls.socket.socket")
    @patch("src.daemonctl.tls.ssl.SSLContext")
    def test_non_strict_mode_continues(self, mock_ctx_cls, mock_sock_cls):
        """非严格模式：指纹不匹配 → 记录警告但返回 SSLSocket"""
        mock_ctx, mock_raw_sock, mock_ssl_sock = _setup_tls_mocks(
            mock_ctx_cls, mock_sock_cls
        )

        kh = _make_mock_known_hosts(
            verify_result=False,
            existing_fp="sha256:bbb222",
        )
        client = TLSClient("10.0.0.1", 18767, kh, tofu_strict=False)

        result = client.connect()

        assert result is mock_ssl_sock
        mock_ssl_sock.close.assert_not_called()


class TestTLSClientConnectNoCert:
    """服务端未提供证书"""

    @patch("src.daemonctl.tls.socket.socket")
    @patch("src.daemonctl.tls.ssl.SSLContext")
    def test_no_cert_raises(self, mock_ctx_cls, mock_sock_cls):
        """getpeercert 返回 None → ConnectionError"""
        mock_ctx, mock_raw_sock, mock_ssl_sock = _setup_tls_mocks(
            mock_ctx_cls, mock_sock_cls, der_cert=None
        )

        kh = _make_mock_known_hosts(verify_result=True)
        client = TLSClient("10.0.0.1", 18767, kh)

        with pytest.raises(ConnectionError, match="服务端未提供证书"):
            client.connect()

        mock_ssl_sock.close.assert_called_once()


class TestTLSClientSslContextConfig:
    """SSLContext 配置测试：CERT_NONE + check_hostname=False"""

    @patch("src.daemonctl.tls.socket.socket")
    @patch("src.daemonctl.tls.ssl.SSLContext")
    def test_cert_none_configured(self, mock_ctx_cls, mock_sock_cls):
        """SSLContext 配置为 CERT_NONE（不验证 CA，用 TOFU 替代）"""
        mock_ctx, mock_raw_sock, mock_ssl_sock = _setup_tls_mocks(
            mock_ctx_cls, mock_sock_cls
        )

        kh = _make_mock_known_hosts(verify_result=True)
        client = TLSClient("10.0.0.1", 18767, kh)
        client.connect()

        # 验证 CERT_NONE 配置（代码中设置 ctx.check_hostname=False, ctx.verify_mode=CERT_NONE）
        assert mock_ctx.check_hostname is False
        assert mock_ctx.verify_mode == ssl.CERT_NONE
        # 验证 wrap_socket 使用 server_hostname
        mock_ctx.wrap_socket.assert_called_once_with(
            mock_raw_sock, server_hostname="10.0.0.1"
        )
