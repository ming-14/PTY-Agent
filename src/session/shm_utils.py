"""共享内存工具 — 认证令牌

提供守护进程与客户端之间的认证令牌共享内存读写操作。
底层共享内存访问统一委托 protocol/shm_utils.py（跨平台 mmap），
本模块只承载认证令牌的业务语义。

守护进程信息区（PID+状态+心跳，单实例检测）见 protocol/shm.py。
"""

import logging
import mmap
from typing import Optional

from ..config import (
    AUTH_TOKEN_NAME,
    AUTH_TOKEN_SIZE,
)
from ..protocol.shm_utils import (
    open_shm,
    close_shm,
    read_bytes,
    write_bytes,
    cleanup_shm,
)

_logger = logging.getLogger("pty-session")


def generate_auth_token() -> str:
    """生成 32 字节随机认证令牌（hex 编码）"""
    import os
    token = os.urandom(32).hex()
    _logger.debug("generate_auth_token: len=%d", len(token))
    return token


def read_auth_token() -> Optional[str]:
    """从共享内存读取认证令牌

    Returns:
        令牌字符串，获取失败返回 None。
    """
    shm = open_shm(AUTH_TOKEN_NAME, AUTH_TOKEN_SIZE, create=False)
    if shm is None:
        _logger.debug("read_auth_token: 共享内存不存在")
        return None
    try:
        data = read_bytes(shm, 0, AUTH_TOKEN_SIZE)
        token = data.rstrip(b"\x00").decode("ascii")
        _logger.debug("read_auth_token: %s...", token[:8] if token else "None")
        return token or None
    except (ValueError, OSError) as e:
        _logger.debug("read_auth_token: failed %s", e)
        return None
    finally:
        close_shm(shm)


def write_auth_token(token: str) -> Optional[mmap.mmap]:
    """将认证令牌写入命名共享内存

    Args:
        token: 认证令牌字符串（hex 编码）。

    Returns:
        mmap 对象（Windows 需保持引用防止回收），Unix 返回 None。
    """
    token_bytes = token.encode("ascii").ljust(AUTH_TOKEN_SIZE, b"\x00")
    _logger.debug("write_auth_token: %s...", token[:8] if token else "None")
    shm = open_shm(AUTH_TOKEN_NAME, AUTH_TOKEN_SIZE)
    if shm is None:
        _logger.error("write_auth_token: 打开共享内存失败")
        return None
    try:
        write_bytes(shm, 0, token_bytes)
    except Exception as e:
        _logger.error("write_auth_token: 写入失败 %s", e)
        close_shm(shm)
        return None
    return shm


def cleanup_auth_shm():
    """清理认证令牌共享内存残留"""
    cleanup_shm(AUTH_TOKEN_NAME)
