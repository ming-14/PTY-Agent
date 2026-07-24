"""Ed25519 密钥加载与指纹计算

提供 OpenSSH 格式兼容的密钥加载、序列化与指纹计算。
支持 ``ssh-keygen -t ed25519`` 生成的密钥与本项目 keygen 生成的密钥互操作。

设计要点：
- 公钥指纹与 ssh-keygen -lf 输出一致（SHA256:base64(sha256(ssh-blob))）
- 私钥文件权限校验（Unix 0600，Windows 跳过）
- authorized_keys 解析返回 指纹->PublicKey 映射，空文件返回空 dict（fail-closed 由调用方决定）
"""

import base64
import hashlib
import logging
import os
from pathlib import Path
from typing import Dict, Union

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_ssh_private_key,
    load_ssh_public_key,
)

_logger = logging.getLogger("pty-auth")

# OpenSSH 公钥指纹前缀，与 ssh-keygen -lf 一致
_FINGERPRINT_PREFIX = "SHA256:"

# 公钥算法标识（预留扩展，当前仅 ed25519）
ALGORITHM_ED25519 = "ed25519"

# OpenSSH 公钥行类型前缀
_SSH_KEY_TYPE_ED25519 = "ssh-ed25519"

# 路径参数类型（支持 str 与 Path）
_PathLike = Union[str, Path]


class PublicKey:
    """Ed25519 公钥

    封装 cryptography Ed25519PublicKey 与计算好的指纹，
    用于服务端 authorized_keys 白名单校验。

    Attributes:
        _key: cryptography Ed25519PublicKey 对象
        _fingerprint: SHA256:base64 指纹（与 ssh-keygen -lf 一致）
    """

    def __init__(self, key: Ed25519PublicKey):
        self._key = key
        self._fingerprint = _compute_fingerprint(key)

    @property
    def key(self) -> Ed25519PublicKey:
        """底层 cryptography 公钥对象"""
        return self._key

    @property
    def fingerprint(self) -> str:
        """公钥指纹（SHA256:base64）"""
        return self._fingerprint

    @classmethod
    def from_ssh_line(cls, line: str) -> "PublicKey":
        """从 OpenSSH authorized_keys 格式一行加载公钥

        格式: ``ssh-ed25519 AAAA... [comment]``

        Args:
            line: authorized_keys 的一行（已 strip）。

        Returns:
            PublicKey 实例。

        Raises:
            ValueError: 格式无效、类型非 ssh-ed25519、或密钥非 Ed25519。
        """
        line = line.strip()
        if not line or line.startswith("#"):
            raise ValueError("空行或注释行")
        parts = line.split(None, 2)
        if len(parts) < 2:
            raise ValueError(f"无效的 authorized_keys 行: {line!r}")
        key_type, key_b64 = parts[0], parts[1]
        if key_type != _SSH_KEY_TYPE_ED25519:
            raise ValueError(f"不支持的公钥类型: {key_type}（仅支持 {_SSH_KEY_TYPE_ED25519}）")
        # load_ssh_public_key 接受 "ssh-ed25519 AAAA..." 字节串
        pub = load_ssh_public_key(f"{key_type} {key_b64}".encode("utf-8"))
        if not isinstance(pub, Ed25519PublicKey):
            raise ValueError(f"密钥类型不是 Ed25519: {key_type}")
        return cls(pub)

    @classmethod
    def from_file(cls, path: _PathLike) -> "PublicKey":
        """从 .pub 文件加载公钥

        Args:
            path: 公钥文件路径（.pub 文件，单行 OpenSSH 格式）。
        """
        path = Path(path).expanduser()
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_ssh_line(f.read())

    def to_ssh_line(self, comment: str = "") -> str:
        """序列化为 OpenSSH authorized_keys 格式行

        Args:
            comment: 可选注释（如 user@host）。

        Returns:
            ``ssh-ed25519 AAAA... [comment]`` 格式字符串。
        """
        # public_bytes(OpenSSH, OpenSSH) 返回 "ssh-ed25519 AAAA..."
        b64_line = self._key.public_bytes(
            Encoding.OpenSSH, PublicFormat.OpenSSH
        ).decode("utf-8")
        if comment:
            return f"{b64_line} {comment}"
        return b64_line


class PrivateKey:
    """Ed25519 私钥

    封装私钥与对应公钥，用于客户端消息签名。

    Attributes:
        _key: cryptography Ed25519PrivateKey 对象
        _public: 对应的 PublicKey（含指纹）
    """

    def __init__(self, key: Ed25519PrivateKey):
        self._key = key
        self._public = PublicKey(key.public_key())

    @property
    def key(self) -> Ed25519PrivateKey:
        """底层 cryptography 私钥对象"""
        return self._key

    @property
    def public_key(self) -> PublicKey:
        """对应的公钥（含指纹）"""
        return self._public

    @property
    def fingerprint(self) -> str:
        """公钥指纹（私钥对应的公钥指纹）"""
        return self._public.fingerprint

    @classmethod
    def from_file(cls, path: _PathLike) -> "PrivateKey":
        """从 OpenSSH 格式私钥文件加载

        支持 ``ssh-keygen -t ed25519`` 生成的 OpenSSH 格式私钥。
        加载前校验文件权限（Unix 必须 0600，Windows 仅检查存在性）。

        Args:
            path: 私钥文件路径。

        Returns:
            PrivateKey 实例。

        Raises:
            FileNotFoundError: 文件不存在。
            PermissionError: Unix 权限非 0600。
            ValueError: 密钥类型非 Ed25519。
        """
        path = Path(path).expanduser()
        _check_private_key_permissions(path)
        data = path.read_bytes()
        key = load_ssh_private_key(data, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("私钥类型不是 Ed25519")
        _logger.debug("已加载 Ed25519 私钥: %s (fp=%s)", path, _short_fp(PublicKey(key.public_key()).fingerprint))
        return cls(key)

    def to_openssh_bytes(self) -> bytes:
        """序列化为 OpenSSH 格式私钥字节（PEM 包装，无密码）"""
        return self._key.private_bytes(
            Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption()
        )


def generate_keypair() -> PrivateKey:
    """生成新的 Ed25519 密钥对

    Returns:
        包含新生成私钥的 PrivateKey 实例（公钥可通过 .public_key 获取）。
    """
    return PrivateKey(Ed25519PrivateKey.generate())


def load_authorized_keys(path: _PathLike) -> Dict[str, PublicKey]:
    """加载 authorized_keys 文件，返回 指纹->PublicKey 映射

    文件格式：每行一个 OpenSSH 公钥（``ssh-ed25519 AAAA... comment``），
    空行与 # 注释行跳过，无效行记录警告并跳过。

    Args:
        path: authorized_keys 文件路径。

    Returns:
        指纹到 PublicKey 的映射。文件不存在或为空时返回空 dict
        （fail-closed 行为由调用方决定）。
    """
    path = Path(path).expanduser()
    if not path.exists():
        _logger.warning("authorized_keys 文件不存在: %s", path)
        return {}
    keys: Dict[str, PublicKey] = {}
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                pub = PublicKey.from_ssh_line(line)
            except ValueError as e:
                _logger.warning("authorized_keys 第 %d 行跳过: %s", lineno, e)
                continue
            if pub.fingerprint in keys:
                _logger.warning(
                    "authorized_keys 第 %d 行指纹重复，覆盖: %s",
                    lineno, _short_fp(pub.fingerprint),
                )
            keys[pub.fingerprint] = pub
    _logger.info("authorized_keys 加载完成: %s 个公钥", len(keys))
    return keys


def _compute_fingerprint(pub: Ed25519PublicKey) -> str:
    """计算公钥指纹，与 ``ssh-keygen -lf`` 输出一致

    算法: SHA256:base64_no_pad(sha256(ssh_public_key_blob))

    Args:
        pub: Ed25519PublicKey 对象。

    Returns:
        ``SHA256:xxxx`` 格式指纹字符串。
    """
    # public_bytes(OpenSSH, OpenSSH) 返回 "ssh-ed25519 AAAA..."
    ssh_line = pub.public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode("utf-8")
    parts = ssh_line.split()
    # parts[1] 是 base64 编码的 SSH 公钥 blob
    blob = base64.b64decode(parts[1])
    digest = hashlib.sha256(blob).digest()
    # OpenSSH 指纹用 base64 去 = 填充
    b64 = base64.b64encode(digest).decode("ascii").rstrip("=")
    return _FINGERPRINT_PREFIX + b64


def _check_private_key_permissions(path: Path) -> None:
    """校验私钥文件权限

    Unix: 必须 0600，否则拒绝加载（与 ssh-keygen 行为一致）。
    Windows: 无传统 Unix 权限位，仅检查文件存在性，权限校验依赖 NTFS ACL。

    Args:
        path: 私钥文件路径。

    Raises:
        FileNotFoundError: 文件不存在。
        PermissionError: Unix 权限非 0600。
    """
    if not path.exists():
        raise FileNotFoundError(f"私钥文件不存在: {path}")
    if os.name == "nt":
        _logger.debug("Windows 平台，跳过 Unix 权限校验: %s", path)
        return
    mode = path.stat().st_mode & 0o777
    if mode != 0o600:
        raise PermissionError(
            f"私钥文件权限过宽 ({oct(mode)})，应为 0600: {path}"
        )


def _short_fp(fingerprint: str) -> str:
    """日志脱敏：指纹只保留前 16 字符，避免完整指纹入日志"""
    return fingerprint[:16] + "..."
