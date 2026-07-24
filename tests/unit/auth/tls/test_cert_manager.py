"""CertificateManager 单元测试

覆盖证书生成、加载、指纹计算、SSLContext 创建。
使用 tmp_path 隔离测试，不污染用户主目录。
"""

import os
import ssl
import pytest

from src.auth.tls.cert_manager import CertificateManager


class TestCertManagerGeneration:
    """证书生成与加载"""

    def test_generate_new_certificate(self, tmp_path):
        """首次调用生成新的证书和私钥文件"""
        cert_file = str(tmp_path / "daemon.crt")
        key_file = str(tmp_path / "daemon.key")

        mgr = CertificateManager(
            cert_dir=str(tmp_path),
            cert_file=cert_file,
            key_file=key_file,
        )
        result = mgr.ensure_certificate()

        cert_path, key_path, fingerprint = result
        assert cert_path == cert_file
        assert key_path == key_file
        assert os.path.exists(cert_file), "证书文件应存在"
        assert os.path.exists(key_file), "私钥文件应存在"
        assert fingerprint.startswith("sha256:"), "指纹应以 sha256: 开头"

    def test_load_existing_certificate(self, tmp_path):
        """二次调用加载已有证书，指纹一致"""
        cert_file = str(tmp_path / "daemon.crt")
        key_file = str(tmp_path / "daemon.key")

        mgr = CertificateManager(
            cert_dir=str(tmp_path),
            cert_file=cert_file,
            key_file=key_file,
        )
        # 首次生成
        _, _, fp1 = mgr.ensure_certificate()
        # 二次加载
        _, _, fp2 = mgr.ensure_certificate()
        assert fp1 == fp2, "同一证书指纹应一致"

    def test_certificate_validity(self, tmp_path):
        """证书文件是有效的 PEM 格式"""
        cert_file = str(tmp_path / "daemon.crt")
        key_file = str(tmp_path / "daemon.key")

        mgr = CertificateManager(
            cert_dir=str(tmp_path),
            cert_file=cert_file,
            key_file=key_file,
        )
        mgr.ensure_certificate()

        # 验证证书文件可被 cryptography 库解析
        from cryptography import x509
        with open(cert_file, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        assert cert is not None
        # 验证私钥文件可被解析
        from cryptography.hazmat.primitives import serialization
        with open(key_file, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        assert key is not None


class TestCertManagerFingerprint:
    """指纹计算"""

    def test_fingerprint_consistency(self, tmp_path):
        """同一证书多次计算指纹结果一致"""
        cert_file = str(tmp_path / "daemon.crt")
        key_file = str(tmp_path / "daemon.key")

        mgr = CertificateManager(
            cert_dir=str(tmp_path),
            cert_file=cert_file,
            key_file=key_file,
        )
        mgr.ensure_certificate()

        fp1 = CertificateManager.compute_fingerprint(cert_file)
        fp2 = CertificateManager.compute_fingerprint(cert_file)
        assert fp1 == fp2

    def test_fingerprint_uniqueness(self, tmp_path):
        """不同证书指纹不同"""
        # 第一套证书
        mgr1 = CertificateManager(
            cert_dir=str(tmp_path / "a"),
            cert_file=str(tmp_path / "a" / "daemon.crt"),
            key_file=str(tmp_path / "a" / "daemon.key"),
        )
        _, _, fp1 = mgr1.ensure_certificate()

        # 第二套证书
        mgr2 = CertificateManager(
            cert_dir=str(tmp_path / "b"),
            cert_file=str(tmp_path / "b" / "daemon.crt"),
            key_file=str(tmp_path / "b" / "daemon.key"),
        )
        _, _, fp2 = mgr2.ensure_certificate()

        assert fp1 != fp2, "不同证书指纹应不同"

    def test_fingerprint_format(self, tmp_path):
        """指纹格式为 sha256:<hex>"""
        cert_file = str(tmp_path / "daemon.crt")
        key_file = str(tmp_path / "daemon.key")

        mgr = CertificateManager(
            cert_dir=str(tmp_path),
            cert_file=cert_file,
            key_file=key_file,
        )
        _, _, fingerprint = mgr.ensure_certificate()

        assert fingerprint.startswith("sha256:")
        hex_part = fingerprint[7:]  # 去掉 "sha256:" 前缀
        assert len(hex_part) == 64, "SHA-256 hex 应为 64 字符"
        int(hex_part, 16)  # 验证是合法的十六进制

    def test_compute_fingerprint_from_der(self, tmp_path):
        """从 DER 编码计算指纹与 PEM 计算结果一致"""
        cert_file = str(tmp_path / "daemon.crt")
        key_file = str(tmp_path / "daemon.key")

        mgr = CertificateManager(
            cert_dir=str(tmp_path),
            cert_file=cert_file,
            key_file=key_file,
        )
        mgr.ensure_certificate()

        # PEM 指纹
        pem_fp = CertificateManager.compute_fingerprint(cert_file)

        # DER 指纹：从 cryptography 库获取 DER 编码
        from cryptography import x509
        with open(cert_file, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        der_bytes = cert.public_bytes(__import__('cryptography').hazmat.primitives.serialization.Encoding.DER)
        der_fp = CertificateManager.compute_fingerprint_from_der(der_bytes)

        assert pem_fp == der_fp, "PEM 和 DER 指纹应一致"


class TestCertManagerSSLContext:
    """SSLContext 创建"""

    def test_create_server_ssl_context(self, tmp_path):
        """create_server_ssl_context 返回可用的 SSLContext"""
        cert_file = str(tmp_path / "daemon.crt")
        key_file = str(tmp_path / "daemon.key")

        mgr = CertificateManager(
            cert_dir=str(tmp_path),
            cert_file=cert_file,
            key_file=key_file,
        )
        mgr.ensure_certificate()

        ctx = mgr.create_server_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)
        # 验证 context 可以加载证书（不抛异常即成功）

    def test_ssl_context_tls_server_protocol(self, tmp_path):
        """SSLContext 使用 TLS Server 协议"""
        cert_file = str(tmp_path / "daemon.crt")
        key_file = str(tmp_path / "daemon.key")

        mgr = CertificateManager(
            cert_dir=str(tmp_path),
            cert_file=cert_file,
            key_file=key_file,
        )
        mgr.ensure_certificate()

        ctx = mgr.create_server_ssl_context()
        # PROTOCOL_TLS_SERVER 是服务端协议
        assert ctx.protocol == ssl.PROTOCOL_TLS_SERVER


class TestCertManagerCustomParams:
    """自定义参数"""

    def test_custom_subject_cn(self, tmp_path):
        """自定义 CN 写入证书"""
        cert_file = str(tmp_path / "daemon.crt")
        key_file = str(tmp_path / "daemon.key")

        mgr = CertificateManager(
            cert_dir=str(tmp_path),
            cert_file=cert_file,
            key_file=key_file,
            subject_cn="my-custom-daemon",
            subject_o="CustomOrg",
        )
        mgr.ensure_certificate()

        from cryptography import x509
        with open(cert_file, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())

        cn = cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)[0].value
        org = cert.subject.get_attributes_for_oid(x509.oid.NameOID.ORGANIZATION_NAME)[0].value
        assert cn == "my-custom-daemon"
        assert org == "CustomOrg"

    def test_custom_validity_days(self, tmp_path):
        """自定义有效期写入证书"""
        cert_file = str(tmp_path / "daemon.crt")
        key_file = str(tmp_path / "daemon.key")

        mgr = CertificateManager(
            cert_dir=str(tmp_path),
            cert_file=cert_file,
            key_file=key_file,
            validity_days=30,
        )
        mgr.ensure_certificate()

        from cryptography import x509
        with open(cert_file, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())

        # 证书有效期应约为 30 天（使用 _utc 变体避免 cryptography 弃用警告）
        delta = cert.not_valid_after_utc - cert.not_valid_before_utc
        assert 29 <= delta.days <= 31, f"有效期应约 30 天，实际 {delta.days} 天"
