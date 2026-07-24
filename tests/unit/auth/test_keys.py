"""Ed25519 密钥加载与指纹计算单元测试

覆盖：
- 密钥生成与往返序列化（OpenSSH 格式）
- 指纹格式与一致性（与 ssh-keygen -lf 算法一致）
- from_ssh_line 解析（含注释、无效行、非 ed25519 类型拒绝）
- load_authorized_keys（多行、空文件、不存在文件、注释行、无效行跳过）
- 私钥文件加载（文件不存在抛 FileNotFoundError）
"""

import base64
import hashlib
from pathlib import Path

import pytest

from src.auth.keys import (
    PublicKey,
    PrivateKey,
    generate_keypair,
    load_authorized_keys,
    _compute_fingerprint,
)


class TestKeypairGeneration:
    """密钥生成与序列化"""

    def test_generate_keypair_returns_private_key(self):
        """generate_keypair 返回 PrivateKey 实例"""
        kp = generate_keypair()
        assert isinstance(kp, PrivateKey)
        assert kp.public_key is not None
        assert isinstance(kp.public_key, PublicKey)

    def test_each_keypair_is_unique(self):
        """每次生成的密钥对不同"""
        kp1 = generate_keypair()
        kp2 = generate_keypair()
        assert kp1.fingerprint != kp2.fingerprint

    def test_private_key_openssh_roundtrip(self, tmp_path):
        """私钥序列化为 OpenSSH 格式后可重新加载，指纹一致"""
        kp = generate_keypair()
        key_path = tmp_path / "id_ed25519"
        key_path.write_bytes(kp.to_openssh_bytes())
        # Windows 无 Unix 权限位，跳过 0600 校验
        reloaded = PrivateKey.from_file(key_path)
        assert reloaded.fingerprint == kp.fingerprint

    def test_public_key_ssh_line_roundtrip(self):
        """公钥序列化为 ssh-ed25519 行后可重新解析"""
        kp = generate_keypair()
        line = kp.public_key.to_ssh_line(comment="test@host")
        assert line.startswith("ssh-ed25519 ")
        assert line.endswith("test@host")
        pub = PublicKey.from_ssh_line(line)
        assert pub.fingerprint == kp.public_key.fingerprint


class TestFingerprint:
    """指纹计算"""

    def test_fingerprint_format(self):
        """指纹格式为 SHA256:base64_no_pad"""
        kp = generate_keypair()
        fp = kp.fingerprint
        assert fp.startswith("SHA256:")
        body = fp[len("SHA256:"):]
        # base64 去 = 填充后长度为 43（sha256 32 字节 → base64 44 字符 → 去 1 个 =）
        assert len(body) == 43
        assert "=" not in body

    def test_fingerprint_matches_openssh_algorithm(self):
        """指纹与手动计算 OpenSSH 算法一致

        OpenSSH 指纹算法: SHA256:base64_no_pad(sha256(ssh_public_key_blob))
        """
        kp = generate_keypair()
        ssh_line = kp.public_key.to_ssh_line()
        parts = ssh_line.split()
        blob = base64.b64decode(parts[1])
        expected = "SHA256:" + base64.b64encode(
            hashlib.sha256(blob).digest()
        ).decode("ascii").rstrip("=")
        assert kp.fingerprint == expected
        assert kp.public_key.fingerprint == expected

    def test_fingerprint_stable_for_same_key(self):
        """同一公钥多次计算指纹结果一致"""
        kp = generate_keypair()
        fp1 = kp.public_key.fingerprint
        # 重新从 ssh 行加载
        pub2 = PublicKey.from_ssh_line(kp.public_key.to_ssh_line())
        assert pub2.fingerprint == fp1


class TestParseSshLine:
    """from_ssh_line 解析"""

    def test_parse_with_comment(self):
        """带注释的行可解析"""
        kp = generate_keypair()
        line = kp.public_key.to_ssh_line(comment="user@host")
        pub = PublicKey.from_ssh_line(line)
        assert pub.fingerprint == kp.public_key.fingerprint

    def test_parse_without_comment(self):
        """不带注释的行可解析"""
        kp = generate_keypair()
        line = kp.public_key.to_ssh_line()
        pub = PublicKey.from_ssh_line(line)
        assert pub.fingerprint == kp.public_key.fingerprint

    def test_parse_empty_line_raises(self):
        """空行抛 ValueError"""
        with pytest.raises(ValueError):
            PublicKey.from_ssh_line("")

    def test_parse_comment_line_raises(self):
        """# 注释行抛 ValueError"""
        with pytest.raises(ValueError):
            PublicKey.from_ssh_line("# this is a comment")

    def test_parse_invalid_format_raises(self):
        """格式无效抛 ValueError"""
        with pytest.raises(ValueError):
            PublicKey.from_ssh_line("ssh-ed25519")

    def test_parse_wrong_key_type_raises(self):
        """非 ssh-ed25519 类型抛 ValueError"""
        # ssh-rsa 开头的行应被拒绝
        with pytest.raises(ValueError, match="不支持的公钥类型"):
            PublicKey.from_ssh_line("ssh-rsa AAAAB3NzaC1yc2E fake")

    def test_parse_leading_trailing_whitespace_stripped(self):
        """前后空白被 strip"""
        kp = generate_keypair()
        line = "  " + kp.public_key.to_ssh_line() + "  \n"
        pub = PublicKey.from_ssh_line(line)
        assert pub.fingerprint == kp.public_key.fingerprint


class TestLoadAuthorizedKeys:
    """load_authorized_keys"""

    def test_load_nonexistent_file_returns_empty(self, tmp_path):
        """不存在的文件返回空 dict"""
        path = tmp_path / "authorized_keys"
        result = load_authorized_keys(path)
        assert result == {}

    def test_load_empty_file_returns_empty(self, tmp_path):
        """空文件返回空 dict"""
        path = tmp_path / "authorized_keys"
        path.write_text("")
        result = load_authorized_keys(path)
        assert result == {}

    def test_load_single_key(self, tmp_path):
        """加载单个公钥"""
        kp = generate_keypair()
        path = tmp_path / "authorized_keys"
        path.write_text(kp.public_key.to_ssh_line("test") + "\n")
        result = load_authorized_keys(path)
        assert len(result) == 1
        assert kp.fingerprint in result

    def test_load_multiple_keys(self, tmp_path):
        """加载多个公钥"""
        kp1 = generate_keypair()
        kp2 = generate_keypair()
        kp3 = generate_keypair()
        path = tmp_path / "authorized_keys"
        path.write_text(
            kp1.public_key.to_ssh_line("k1") + "\n"
            + kp2.public_key.to_ssh_line("k2") + "\n"
            + kp3.public_key.to_ssh_line("k3") + "\n"
        )
        result = load_authorized_keys(path)
        assert len(result) == 3
        assert kp1.fingerprint in result
        assert kp2.fingerprint in result
        assert kp3.fingerprint in result

    def test_load_skips_comments_and_blanks(self, tmp_path):
        """跳过注释行与空行"""
        kp = generate_keypair()
        path = tmp_path / "authorized_keys"
        path.write_text(
            "# header comment\n"
            "\n"
            + kp.public_key.to_ssh_line("real") + "\n"
            "\n"
            "# tail comment\n"
        )
        result = load_authorized_keys(path)
        assert len(result) == 1
        assert kp.fingerprint in result

    def test_load_skips_invalid_lines(self, tmp_path):
        """跳过无效行（非 ed25519）"""
        kp = generate_keypair()
        path = tmp_path / "authorized_keys"
        path.write_text(
            "ssh-rsa AAAAB3NzaC1yc2E fake\n"
            + kp.public_key.to_ssh_line("real") + "\n"
            "garbage line\n"
        )
        result = load_authorized_keys(path)
        assert len(result) == 1
        assert kp.fingerprint in result


class TestPrivateKeyLoading:
    """私钥文件加载"""

    def test_load_nonexistent_raises(self, tmp_path):
        """文件不存在抛 FileNotFoundError"""
        path = tmp_path / "missing"
        with pytest.raises(FileNotFoundError):
            PrivateKey.from_file(path)

    def test_load_wrong_type_raises(self, tmp_path):
        """非 Ed25519 私钥抛 ValueError

        用 cryptography 生成 RSA 私钥（OpenSSH 格式）验证拒绝。
        """
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PrivateFormat, NoEncryption,
        )
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key_path = tmp_path / "id_rsa"
        key_path.write_bytes(
            rsa_key.private_bytes(
                Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption()
            )
        )
        with pytest.raises(ValueError, match="不是 Ed25519"):
            PrivateKey.from_file(key_path)


class TestPublicKeyFromFile:
    """PublicKey.from_file"""

    def test_load_pub_file(self, tmp_path):
        """从 .pub 文件加载公钥"""
        kp = generate_keypair()
        pub_path = tmp_path / "id_ed25519.pub"
        pub_path.write_text(kp.public_key.to_ssh_line("test") + "\n")
        pub = PublicKey.from_file(pub_path)
        assert pub.fingerprint == kp.fingerprint
