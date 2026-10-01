"""共享内存底层工具 — 跨平台 mmap 操作

提供跨平台（Windows 命名 mmap / Unix 文件 mmap）的共享内存创建、
打开、读写与清理操作。所有进程间通信基于本模块，无 socket、无端口。

Windows: 使用 ``mmap.mmap(-1, size, tagname=...)`` 创建命名共享内存，
         `Local\\` 前缀限定同会话访问，跨用户隔离由内核保证。
Unix:    使用 ``~/.pty-agent/shm_<name>`` 文件 + mmap，文件权限 0600。

本模块为底层工具层，零业务逻辑，被通信协议（shm.py）与上层复用。
"""

import logging
import os
import mmap
from typing import Optional

from ..config import DATA_DIR, IS_WINDOWS

_logger = logging.getLogger("pty-protocol")


def _unix_path(name: str) -> str:
    """将 Windows tagname 转换为 Unix 文件路径

    Args:
        name: 共享内存名（如 "Local\\PTYAgentMailbox"）。

    Returns:
        Unix 下对应的文件路径（位于 DATA_DIR 下）。
    """
    safe = name.replace("Local\\", "").replace("\\", "_").replace("/", "_")
    return os.path.join(DATA_DIR, f"shm_{safe}")


def open_shm(name: str, size: int, create: bool = True) -> Optional[mmap.mmap]:
    """打开（必要时创建）一个共享内存区域

    Args:
        name:   共享内存名。
        size:   区域大小（字节）。
        create: 不存在时是否创建。False 且不存在时返回 None。

    Returns:
        mmap 对象，失败或不存在时返回 None。
        调用方必须保持引用，否则 Windows 命名映射可能被回收。
    """
    try:
        if IS_WINDOWS:
            # 命名 mmap：不存在则自动创建，存在则打开（长度以首次创建为准）
            shm = mmap.mmap(-1, size, tagname=name)
            return shm
        # Unix：文件 mmap
        path = _unix_path(name)
        if not create and not os.path.exists(path):
            return None
        os.makedirs(DATA_DIR, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.fstat(fd).st_size < size:
                os.ftruncate(fd, size)
            return mmap.mmap(fd, size, access=mmap.ACCESS_WRITE)
        finally:
            os.close(fd)
    except (FileNotFoundError, OSError, ValueError) as e:
        _logger.debug("open_shm(%r) 失败: %s", name, e)
        return None


def close_shm(shm: Optional[mmap.mmap]):
    """关闭共享内存对象（容错）"""
    if shm is None:
        return
    try:
        shm.close()
    except (ValueError, OSError):
        pass


def read_bytes(shm: mmap.mmap, offset: int, length: int) -> bytes:
    """从共享内存读取原始字节

    Args:
        shm:    mmap 对象。
        offset: 起始偏移。
        length: 读取长度。

    Returns:
        读取的字节（越界部分补 \\x00）。
    """
    size = shm.size()
    if offset >= size or length <= 0:
        return b""
    length = min(length, size - offset)
    shm.seek(offset)
    return shm.read(length)


def write_bytes(shm: mmap.mmap, offset: int, data: bytes):
    """向共享内存写入原始字节（越界部分忽略）

    Args:
        shm:    mmap 对象。
        offset: 起始偏移。
        data:   待写入字节。
    """
    size = shm.size()
    if offset >= size:
        return
    length = min(len(data), size - offset)
    if length <= 0:
        return
    shm.seek(offset)
    shm.write(data[:length])


def read_text(shm: mmap.mmap, offset: int, length: int) -> str:
    """读取 ASCII 文本字段（去掉 \\x00 填充）"""
    return read_bytes(shm, offset, length).rstrip(b"\x00").decode("ascii", errors="ignore")


def write_text(shm: mmap.mmap, offset: int, text: str, field_size: int):
    """写入 ASCII 文本字段（右侧 \\x00 填充到 field_size）"""
    data = text.encode("ascii", errors="ignore")[:field_size]
    write_bytes(shm, offset, data.ljust(field_size, b"\x00"))


def cleanup_shm(name: str):
    """清理共享内存残留（Unix 删除文件，Windows 置零）

    Args:
        name: 共享内存名。
    """
    if IS_WINDOWS:
        try:
            shm = mmap.mmap(-1, 1, tagname=name)
            shm.write(b"\x00")
            shm.close()
        except (FileNotFoundError, OSError, ValueError):
            pass
        return
    path = _unix_path(name)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
