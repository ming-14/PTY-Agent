"""daemonctl — TLS 客户端连接器（pubkey 跨机模式）

建立 TLS 连接并执行 TOFU 证书验证，用于连接远程 daemon：
首次连接自动信任证书指纹，后续连接比对指纹（类似 SSH known_hosts）。

TOFU (Trust On First Use) 信任模型：
- 首次连接：自动信任服务端证书指纹并存储到 known_hosts 文件
- 后续连接：比对指纹，不匹配则拒绝（严格模式）或仅警告（非严格模式）
- 类似 SSH 的 StrictHostKeyChecking=accept-new
"""

import logging
import socket
import ssl

from ..auth.tls.cert_manager import CertificateManager
from ..auth.tls.known_hosts import KnownHosts

_logger = logging.getLogger("pty-daemonctl")


class TLSClient:
    """TLS 客户端连接器 — 建立 TLS 连接 + TOFU 证书验证

    使用 CERT_NONE（不验证 CA）+ 自定义 TOFU 指纹验证替代传统 CA 链验证。
    适用于自签证书场景，无需部署 CA 证书到客户端。

    Attributes:
        host: 服务端主机名或 IP 地址。
        port: 服务端 TLS 端口。
        known_hosts: TOFU 信任存储管理器（KnownHosts 实例）。
        tofu_strict: True=指纹不匹配时拒绝连接，False=仅记录警告不拒绝。
        timeout: TCP 连接超时秒数。
    """

    def __init__(
        self,
        host: str,
        port: int,
        known_hosts: KnownHosts,
        tofu_strict: bool = True,
        timeout: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.known_hosts = known_hosts
        self.tofu_strict = tofu_strict
        self.timeout = timeout

    def connect(self) -> ssl.SSLSocket:
        """建立 TLS 连接并验证服务端证书指纹

        流程：
        1. 创建 CERT_NONE 的 SSLContext（不验证 CA，用 TOFU 替代）
        2. TCP 连接服务端 + TLS 握手
        3. 获取服务端 DER 证书并计算 SHA-256 指纹
        4. TOFU 验证：首次自动信任，后续比对指纹

        Returns:
            已完成 TLS 握手的 SSLSocket。

        Raises:
            ConnectionError: 证书指纹不匹配且 tofu_strict=True，或服务端未提供证书。
            OSError: TCP 连接或 TLS 握手失败。
        """
        # CERT_NONE: 不验证 CA 链，用 TOFU 指纹验证替代
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # TCP 连接 + TLS 包装
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.settimeout(self.timeout)
        raw_sock.connect((self.host, self.port))
        _logger.debug("TCP 已连接 %s:%d，开始 TLS 握手", self.host, self.port)

        ssl_sock = ctx.wrap_socket(raw_sock, server_hostname=self.host)

        # 获取服务端证书 DER 编码并计算指纹
        der_cert = ssl_sock.getpeercert(binary_form=True)
        if der_cert is None:
            ssl_sock.close()
            raise ConnectionError(f"服务端未提供证书: {self.host}:{self.port}")

        fingerprint = CertificateManager.compute_fingerprint_from_der(der_cert)
        _logger.debug("TLS 握手完成，服务端证书指纹: %s...", fingerprint[:32])

        # TOFU 验证（KnownHosts.verify: 首次自动信任，后续比对）
        if not self.known_hosts.verify(self.host, self.port, fingerprint):
            existing = self.known_hosts.get(self.host, self.port)
            msg = (
                f"证书指纹不匹配: {self.host}:{self.port}\n"
                f"已知: {existing}\n"
                f"实际: {fingerprint}\n"
                f"如确认安全，请删除 known_hosts 中对应条目"
            )
            if self.tofu_strict:
                ssl_sock.close()
                raise ConnectionError(msg)
            else:
                _logger.warning("TOFU 非严格模式，继续连接: %s", msg)

        return ssl_sock
