"""TLS 自签证书管理（类似 SSH host key）

daemon 首次启动自动生成自签证书，后续启动加载已有证书。
证书指纹用于客户端 TOFU 信任验证。

设计要点：
- 使用 RSA 2048 密钥（TLS 兼容性最佳，不依赖 Ed25519 TLS 支持）
- 证书为自签 CA 证书（BasicConstraints ca=True）
- 指纹格式: "sha256:<hex>"（与 OpenSSH known_hosts 一致）
- 私钥文件权限限制为仅所有者可读写
"""

import datetime
import logging
import os
import ssl
from typing import Tuple

_logger = logging.getLogger("pty-auth-tls")


class CertificateManager:
    """TLS 自签证书管理器

    管理 daemon 的 TLS 证书生命周期：
    1. 首次启动时自动生成自签证书
    2. 后续启动时加载已有证书
    3. 计算证书指纹供 TOFU 验证
    4. 创建服务端 SSLContext

    Attributes:
        cert_dir: 证书存储目录。
        cert_file: 证书文件路径。
        key_file: 私钥文件路径。
        validity_days: 证书有效期（天）。
        subject_cn: 证书 Common Name。
    """

    def __init__(
        self,
        cert_dir: str,
        cert_file: str,
        key_file: str,
        validity_days: int = 365,
        subject_cn: str = "pty-agent-daemon",
        subject_o: str = "PTY-Agent",
    ):
        self.cert_dir = os.path.expanduser(cert_dir)
        self.cert_file = os.path.expanduser(cert_file)
        self.key_file = os.path.expanduser(key_file)
        self.validity_days = validity_days
        self.subject_cn = subject_cn
        self.subject_o = subject_o

    def ensure_certificate(self) -> Tuple[str, str, str]:
        """确保证书存在，不存在则生成自签证书

        Returns:
            (cert_path, key_path, fingerprint) 三元组。
            fingerprint 格式为 "sha256:<hex>"。
        """
        if os.path.exists(self.cert_file) and os.path.exists(self.key_file):
            fingerprint = self.compute_fingerprint(self.cert_file)
            _logger.info(
                "加载已有 TLS 证书: %s (指纹: %s...)",
                self.cert_file,
                fingerprint[:32],
            )
            return (self.cert_file, self.key_file, fingerprint)

        return self._generate_self_signed()

    def _generate_self_signed(self) -> Tuple[str, str, str]:
        """生成 RSA 2048 自签证书

        使用 cryptography 库构建 X.509 自签 CA 证书，
        写入 PEM 格式的证书文件和 PKCS8 私钥文件。
        """
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        _logger.info("生成新的自签 TLS 证书: %s", self.cert_file)

        # 生成 RSA 2048 私钥
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        # 构建自签证书
        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, self.subject_cn),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, self.subject_o),
            ]
        )

        not_before = datetime.datetime.now(tz=datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_before + datetime.timedelta(days=self.validity_days))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None),
                critical=True,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=True,
                    key_cert_sign=True,
                    key_agreement=False,
                    content_commitment=False,
                    data_encipherment=False,
                    encipher_only=False,
                    decipher_only=False,
                    crl_sign=False,
                ),
                critical=True,
            )
            .sign(private_key, hashes.SHA256())
        )

        # 确保证书目录存在
        os.makedirs(self.cert_dir, exist_ok=True)

        # 写入私钥（PKCS8 格式，无加密）
        key_data = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self._write_key_file(key_data)

        # 写入证书（PEM 格式）
        cert_data = cert.public_bytes(serialization.Encoding.PEM)
        with open(self.cert_file, "wb") as f:
            f.write(cert_data)

        fingerprint = self.compute_fingerprint(self.cert_file)
        _logger.info("TLS 证书已生成 (指纹: %s...)", fingerprint[:32])
        return (self.cert_file, self.key_file, fingerprint)

    def _write_key_file(self, key_data: bytes):
        """写入私钥文件并设置权限

        Windows 上通过文件属性限制访问；Unix 上设置 0600 权限。
        """
        with open(self.key_file, "wb") as f:
            f.write(key_data)

        # Unix 权限限制
        if os.name != "nt":
            os.chmod(self.key_file, 0o600)

    def create_server_ssl_context(self) -> ssl.SSLContext:
        """创建服务端 SSLContext

        加载证书和私钥，配置为服务端 TLS 上下文。

        Returns:
            配置好证书链的 ssl.SSLContext 实例。
        """
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert_file, self.key_file)
        _logger.debug("服务端 SSLContext 已创建")
        return ctx

    @staticmethod
    def compute_fingerprint(cert_path: str) -> str:
        """计算证书 DER 编码的 SHA-256 指纹

        Args:
            cert_path: 证书文件路径（PEM 格式）。

        Returns:
            指纹字符串，格式为 "sha256:<hex>"。
        """
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())

        fp = cert.fingerprint(hashes.SHA256())
        return "sha256:" + fp.hex()

    @staticmethod
    def compute_fingerprint_from_der(der_cert: bytes) -> str:
        """从 DER 编码的证书字节计算 SHA-256 指纹

        用于客户端从 SSL socket 获取的 DER 证书计算指纹。

        Args:
            der_cert: DER 编码的证书字节串。

        Returns:
            指纹字符串，格式为 "sha256:<hex>"。
        """
        import hashlib

        return "sha256:" + hashlib.sha256(der_cert).hexdigest()
