"""共享内存工具 — 认证令牌、端口与 PID 传递

提供守护进程与客户端之间的共享内存读写操作。
Windows 使用命名 mmap，Unix 回退文件。
所有进程间信息（PID、端口、认证令牌）均通过共享内存传递，不写磁盘文件。
"""

import logging
import os
import mmap
from typing import Optional

from ..config import (
    IS_WINDOWS,
    DEFAULT_DAEMON_PORT,
    MMAP_NAME,
    MMAP_SIZE,
    AUTH_TOKEN_NAME,
    AUTH_TOKEN_SIZE,
)

_logger = logging.getLogger("pty-session")


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
        from ..config import DATA_DIR
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
        from ..config import DATA_DIR
        os.makedirs(DATA_DIR, exist_ok=True)
        token_file = os.path.join(DATA_DIR, "daemon.auth")
        with open(token_file, "w") as f:
            f.write(token)
        if not IS_WINDOWS:
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
        from ..config import DATA_DIR
        token_file = os.path.join(DATA_DIR, "daemon.auth")
        try:
            if os.path.exists(token_file):
                os.remove(token_file)
        except OSError:
            pass


def read_daemon_info_from_shm() -> Optional[tuple]:
    """从共享内存读取守护进程 PID 和端口号

    Returns:
        (pid, port) 元组，读取失败返回 None。
    """
    if IS_WINDOWS:
        try:
            shm = mmap.mmap(-1, MMAP_SIZE, tagname=MMAP_NAME)
            data = shm.read(MMAP_SIZE)
            shm.close()
            text = data.rstrip(b"\x00").decode("ascii")
            if not text:
                return None
            parts = text.split(":")
            if len(parts) != 2:
                return None
            pid = int(parts[0])
            port = int(parts[1])
            _logger.debug("read_daemon_info_from_shm: pid=%d port=%d", pid, port)
            return (pid, port)
        except (FileNotFoundError, ValueError, OSError) as e:
            _logger.debug("read_daemon_info_from_shm: failed %s", e)
            return None
    else:
        from ..config import PORT_FILE
        try:
            with open(PORT_FILE, "r") as f:
                content = f.read().strip()
                if not content:
                    return None
                parts = content.split(":")
                if len(parts) != 2:
                    return None
                pid = int(parts[0])
                port = int(parts[1])
                _logger.debug("read_daemon_info_from_shm (file): pid=%d port=%d", pid, port)
                return (pid, port)
        except (FileNotFoundError, ValueError, OSError) as e:
            _logger.debug("read_daemon_info_from_shm (file): failed %s", e)
            return None


def read_port_from_shm() -> int:
    """从共享内存读取守护进程端口号（便捷方法）

    Returns:
        端口号。读取失败返回 DEFAULT_DAEMON_PORT。
    """
    info = read_daemon_info_from_shm()
    if info is None:
        return DEFAULT_DAEMON_PORT
    return info[1]


def write_daemon_info_to_shm(pid: int, port: int) -> Optional[mmap.mmap]:
    """将守护进程 PID 和端口号写入命名共享内存

    格式: "PID:PORT"（如 "5488:53670"）

    Args:
        pid: 守护进程 PID。
        port: 端口号。

    Returns:
        mmap 对象（Windows，调用方必须保持引用，否则共享内存被销毁），
        Unix 返回 None。
    """
    text = f"{pid}:{port}"
    data = text.encode("ascii").ljust(MMAP_SIZE, b"\x00")
    _logger.info("write_daemon_info_to_shm: pid=%d port=%d", pid, port)
    if IS_WINDOWS:
        shm = mmap.mmap(-1, MMAP_SIZE, tagname=MMAP_NAME)
        shm.write(data)
        return shm
    else:
        from ..config import DATA_DIR, PORT_FILE
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PORT_FILE, "w") as f:
            f.write(text)
        return None


def cleanup_port_shm():
    """清理端口共享内存残留"""
    if IS_WINDOWS:
        try:
            shm = mmap.mmap(-1, MMAP_SIZE, tagname=MMAP_NAME)
            shm.write(b"\x00" * MMAP_SIZE)
            shm.close()
        except (FileNotFoundError, OSError):
            pass
    else:
        from ..config import PORT_FILE
        try:
            if os.path.exists(PORT_FILE):
                os.remove(PORT_FILE)
        except OSError:
            pass
