"""共享内存 IPC — 守护进程与客户端之间的进程间通信

提供守护进程与客户端之间的共享内存读写操作。
Windows 使用命名 mmap，Unix 回退文件。

两类共享数据（仅认证令牌与 HMAC 密钥；daemon 端口为固定端口配置，
不再经共享内存发现）：
- 认证令牌：守护进程生成并轮换，客户端读取用于 TCP 认证
- HMAC 密钥：守护进程生成，客户端加载用于消息签名验证
"""

import mmap
import os
import time
from typing import Optional

from ..config.common import IS_WINDOWS
from ..config.shared import (
    AUTH_TOKEN_NAME,
    AUTH_TOKEN_SIZE,
    HMAC_KEY_NAME,
    HMAC_KEY_SIZE,
)
from ..logging import get_logger

_logger = get_logger("pty-ipc")

# 令牌 SHM seqlock 布局：[seq 1 字节][token 数据 63 字节]
# seq 偶数 = 稳定可读；奇数 = 写入中。写者先置奇 → 写数据 → 再置偶；
# 读者读 seq → 数据 → 复核 seq，不一致或为奇则重读。单字节写天然原子，
# 由此消除令牌轮换窗口期并发读者的撕裂读（半个旧 token + 半个新 token
# 既非旧也非新 → Authentication failed）。
_TOKEN_SEQ_OFFSET = 0
_TOKEN_DATA_OFFSET = 1
_TOKEN_DATA_SIZE = AUTH_TOKEN_SIZE - 1
_TOKEN_READ_RETRIES = 4


def _create_restricted_mmap(size: int, tagname: str) -> mmap.mmap:
    """创建受限共享内存（仅当前用户可访问）

    Windows: 使用 NULL DACL 让 OS 默认限制为当前用户会话。
    Local\\ 前缀已确保同会话隔离，此处不做额外 DACL 限制
    以避免 ctypes 复杂性和兼容性问题。
    """
    return mmap.mmap(-1, size, tagname=tagname)


# ═══════════════════════════════════════════════════════════════
#  认证令牌
# ═══════════════════════════════════════════════════════════════


def generate_auth_token() -> str:
    """生成 31 字节随机认证令牌（hex 编码，62 字符 ≤ SHM 数据区 63 字节）"""
    token = os.urandom(31).hex()
    _logger.debug("generate_auth_token: len=%d", len(token))
    return token


def _publish_token(shm: mmap.mmap, token: str) -> None:
    """按 seqlock 协议发布令牌到共享内存（daemon 为唯一写者）

    seq 置奇（写入中）→ 写数据区 → seq 置偶（完成）。
    读者据此检测撕裂读；跨字节的数据写不再是原子性要求。
    """
    data = token.encode("ascii")[:_TOKEN_DATA_SIZE].ljust(_TOKEN_DATA_SIZE, b"\x00")
    seq = shm[_TOKEN_SEQ_OFFSET]
    shm.seek(_TOKEN_SEQ_OFFSET)
    shm.write(bytes([(seq + 1) & 0xFF]))  # 写入中（奇数）
    shm.seek(_TOKEN_DATA_OFFSET)
    shm.write(data)
    shm.seek(_TOKEN_SEQ_OFFSET)
    shm.write(bytes([(seq + 2) & 0xFF]))  # 完成（偶数）


def read_auth_token() -> Optional[str]:
    """从共享内存读取认证令牌（seqlock 复核，撕裂读自动重读）

    Returns:
        令牌字符串，获取失败返回 None。
    """
    if IS_WINDOWS:
        try:
            shm = _create_restricted_mmap(AUTH_TOKEN_SIZE, AUTH_TOKEN_NAME)
            try:
                for _ in range(_TOKEN_READ_RETRIES):
                    seq1 = shm[_TOKEN_SEQ_OFFSET]
                    if seq1 & 1:  # 写者正在发布，稍后重读
                        time.sleep(0.001)
                        continue
                    shm.seek(_TOKEN_DATA_OFFSET)
                    data = shm.read(_TOKEN_DATA_SIZE)
                    if shm[_TOKEN_SEQ_OFFSET] != seq1:  # 撕裂读，重读
                        continue
                    token = data.rstrip(b"\x00").decode("ascii")
                    _logger.debug(
                        "read_auth_token: %s...", token[:8] if token else "None"
                    )
                    return token or None
                _logger.warning("read_auth_token: 持续撕裂读，放弃本次读取")
                return None
            finally:
                shm.close()
        except (FileNotFoundError, OSError) as e:
            _logger.debug("read_auth_token: failed %s", e)
            return None
    else:
        from ..config.common import DATA_DIR

        token_file = os.path.join(DATA_DIR, "daemon.auth")
        try:
            with open(token_file, "r") as f:
                token = f.read().strip() or None
                _logger.debug(
                    "read_auth_token (file): %s...", token[:8] if token else "None"
                )
                return token
        except (FileNotFoundError, OSError) as e:
            _logger.debug("read_auth_token (file): failed %s", e)
            return None


def write_auth_token(token: str) -> Optional[mmap.mmap]:
    """将认证令牌写入命名共享内存（seqlock 发布）

    Args:
        token: 认证令牌字符串（hex 编码）。

    Returns:
        mmap 对象（Windows），Unix 返回 None。
    """
    _logger.debug("write_auth_token: %s...", token[:8] if token else "None")
    if IS_WINDOWS:
        shm = _create_restricted_mmap(AUTH_TOKEN_SIZE, AUTH_TOKEN_NAME)
        _publish_token(shm, token)
        return shm
    else:
        from ..config.common import DATA_DIR

        os.makedirs(DATA_DIR, exist_ok=True)
        token_file = os.path.join(DATA_DIR, "daemon.auth")
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, token.encode("ascii"))
        finally:
            os.close(fd)
        return None


def update_auth_token(shm: mmap.mmap, token: str) -> None:
    """原地更新已创建的令牌共享内存（seqlock 发布，免重建 mmap）

    Args:
        shm: write_auth_token 返回的 mmap 对象（Windows）。
        token: 新的认证令牌字符串（hex 编码）。
    """
    _publish_token(shm, token)


def cleanup_auth_shm():
    """清理认证令牌共享内存残留"""
    if IS_WINDOWS:
        try:
            shm = _create_restricted_mmap(AUTH_TOKEN_SIZE, AUTH_TOKEN_NAME)
            shm.write(b"\x00" * AUTH_TOKEN_SIZE)
            shm.close()
        except (FileNotFoundError, OSError):
            pass
    else:
        from ..config.common import DATA_DIR

        token_file = os.path.join(DATA_DIR, "daemon.auth")
        try:
            if os.path.exists(token_file):
                os.remove(token_file)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════
#  HMAC 密钥
# ═══════════════════════════════════════════════════════════════


def read_hmac_key() -> Optional[bytes]:
    """从共享内存读取 HMAC 密钥

    Returns:
        HMAC 密钥（bytes），读取失败返回 None。
    """
    if IS_WINDOWS:
        try:
            shm = _create_restricted_mmap(HMAC_KEY_SIZE, HMAC_KEY_NAME)
            data = shm.read(HMAC_KEY_SIZE)
            shm.close()
            key_hex = data.rstrip(b"\x00").decode("ascii")
            if key_hex:
                _logger.debug("read_hmac_key: loaded from shm")
                return bytes.fromhex(key_hex)
            else:
                _logger.warning("read_hmac_key: shm key_hex is empty")
                return None
        except (FileNotFoundError, OSError) as e:
            _logger.debug("read_hmac_key: failed %s", e)
            return None
    else:
        from ..config.common import DATA_DIR

        hmac_file = os.path.join(DATA_DIR, "daemon.hmac")
        try:
            with open(hmac_file, "r") as f:
                key_hex = f.read().strip()
            if key_hex:
                _logger.debug("read_hmac_key: loaded from file")
                return bytes.fromhex(key_hex)
            else:
                _logger.warning("read_hmac_key: file key_hex is empty")
                return None
        except (FileNotFoundError, OSError) as e:
            _logger.debug("read_hmac_key: failed %s", e)
            return None


def write_hmac_key(key: bytes) -> Optional[mmap.mmap]:
    """将 HMAC 密钥写入命名共享内存

    Args:
        key: HMAC 密钥（原始 bytes）。

    Returns:
        mmap 对象（Windows，调用方必须保持引用），Unix 返回 None。
    """
    hmac_bytes = key.hex().encode("ascii").ljust(HMAC_KEY_SIZE, b"\x00")
    if IS_WINDOWS:
        shm = _create_restricted_mmap(HMAC_KEY_SIZE, HMAC_KEY_NAME)
        shm.write(hmac_bytes)
        _logger.info("write_hmac_key: HMAC 密钥已发布")
        return shm
    else:
        from ..config.common import DATA_DIR

        os.makedirs(DATA_DIR, exist_ok=True)
        hmac_file = os.path.join(DATA_DIR, "daemon.hmac")
        fd = os.open(hmac_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key.hex().encode("ascii"))
        finally:
            os.close(fd)
        _logger.info("write_hmac_key: HMAC 密钥已写入文件")
        return None


def cleanup_hmac_shm():
    """清理 HMAC 密钥共享内存残留"""
    if IS_WINDOWS:
        try:
            shm = _create_restricted_mmap(HMAC_KEY_SIZE, HMAC_KEY_NAME)
            shm.write(b"\x00" * HMAC_KEY_SIZE)
            shm.close()
        except (FileNotFoundError, OSError):
            pass
    else:
        from ..config.common import DATA_DIR

        hmac_file = os.path.join(DATA_DIR, "daemon.hmac")
        try:
            if os.path.exists(hmac_file):
                os.remove(hmac_file)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════
#  批量清理
# ═══════════════════════════════════════════════════════════════


def cleanup_all_shm():
    """清理所有共享内存残留（认证令牌 + HMAC 密钥）"""
    cleanup_auth_shm()
    cleanup_hmac_shm()
