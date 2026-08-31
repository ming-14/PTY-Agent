"""共享内存工具 — 认证令牌

提供守护进程与客户端之间的认证令牌共享内存读写操作。
Windows 使用命名 mmap，Unix 使用文件回退。

守护进程信息区（PID+状态+心跳，单实例检测）见 protocol/shm.py。
"""

import logging
import os
import mmap
from typing import Optional

from ..config import (
    IS_WINDOWS,
    AUTH_TOKEN_NAME,
    AUTH_TOKEN_SIZE,
    DATA_DIR,
)

_logger = logging.getLogger("pty-session")

# ============================================================
#  认证令牌（同现有机制）
# ============================================================


def generate_auth_token() -> str:
    """生成 32 字节随机认证令牌（hex 编码）"""
    import os as _os
    token = _os.urandom(32).hex()
    _logger.debug("generate_auth_token: len=%d", len(token))
    return token


def read_auth_token() -> Optional[str]:
    """从共享内存读取认证令牌

    Returns:
        令牌字符串，获取失败返回 None。
    """
    if IS_WINDOWS:
        try:
            shm = mmap.mmap(-1, AUTH_TOKEN_SIZE, tagname=AUTH_TOKEN_NAME)
            data = shm.read(AUTH_TOKEN_SIZE)
            shm.close()
            token = data.rstrip(b"\x00").decode("ascii")
            _logger.debug("read_auth_token: %s...", token[:8] if token else "None")
            return token or None
        except (FileNotFoundError, OSError) as e:
            _logger.debug("read_auth_token: failed %s", e)
            return None
    else:
        token_file = os.path.join(DATA_DIR, "daemon.auth")
        try:
            with open(token_file, "r") as f:
                token = f.read().strip() or None
                _logger.debug("read_auth_token (file): %s...", token[:8] if token else "None")
                return token
        except (FileNotFoundError, OSError) as e:
            _logger.debug("read_auth_token (file): failed %s", e)
            return None


def write_auth_token(token: str) -> Optional[mmap.mmap]:
    """将认证令牌写入命名共享内存

    Args:
        token: 认证令牌字符串（hex 编码）。

    Returns:
        mmap 对象（Windows），Unix 返回 None。
    """
    token_bytes = token.encode("ascii").ljust(AUTH_TOKEN_SIZE, b"\x00")
    _logger.debug("write_auth_token: %s...", token[:8] if token else "None")
    if IS_WINDOWS:
        shm = mmap.mmap(-1, AUTH_TOKEN_SIZE, tagname=AUTH_TOKEN_NAME)
        shm.write(token_bytes)
        return shm
    else:
        os.makedirs(DATA_DIR, exist_ok=True)
        token_file = os.path.join(DATA_DIR, "daemon.auth")
        with open(token_file, "w") as f:
            f.write(token)
        os.chmod(token_file, 0o600)
        return None


def cleanup_auth_shm():
    """清理认证令牌共享内存残留"""
    if IS_WINDOWS:
        try:
            shm = mmap.mmap(-1, AUTH_TOKEN_SIZE, tagname=AUTH_TOKEN_NAME)
            shm.write(b"\x00" * AUTH_TOKEN_SIZE)
            shm.close()
        except (FileNotFoundError, OSError):
            pass
    else:
        token_file = os.path.join(DATA_DIR, "daemon.auth")
        try:
            if os.path.exists(token_file):
                os.remove(token_file)
        except OSError:
            pass