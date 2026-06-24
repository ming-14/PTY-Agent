"""共享内存工具 — 认证令牌、端口传递与 PID 文件

提供守护进程与客户端之间的共享内存读写操作，
以及 PID 文件管理用于单实例检查和孤儿进程清理。
"""

import logging
import os
import mmap
from typing import Optional

from ..config import (
    IS_WINDOWS,
    DEFAULT_DAEMON_PORT,
    DATA_DIR,
    PORT_FILE,
    PID_FILE,
    MMAP_NAME,
    MMAP_SIZE,
    AUTH_TOKEN_NAME,
    AUTH_TOKEN_SIZE,
)

_logger = logging.getLogger("pty-session")


def generate_auth_token() -> str:
    """生成 32 字节随机认证令牌（hex 编码）"""
    token = os.urandom(32).hex()
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
        # Unix 上限制令牌文件权限为仅所有者可读写
        if not IS_WINDOWS:
            os.chmod(token_file, 0o600)
        return None


def cleanup_auth_shm():
    """清理认证令牌共享内存残留（Unix 令牌文件）"""
    if not IS_WINDOWS:
        token_file = os.path.join(DATA_DIR, "daemon.auth")
        try:
            if os.path.exists(token_file):
                os.remove(token_file)
        except OSError:
            pass


def read_port_from_shm() -> int:
    """从共享内存读取守护进程端口号

    Returns:
        端口号。读取失败时返回 DEFAULT_DAEMON_PORT。
    """
    if IS_WINDOWS:
        try:
            shm = mmap.mmap(-1, MMAP_SIZE, tagname=MMAP_NAME)
            data = shm.read(MMAP_SIZE)
            shm.close()
            port_str = data.rstrip(b"\x00").decode("ascii")
            port = int(port_str) if port_str else DEFAULT_DAEMON_PORT
            _logger.debug("read_port_from_shm: %d", port)
            return port
        except (FileNotFoundError, ValueError, OSError) as e:
            _logger.debug("read_port_from_shm: failed %s", e)
            return DEFAULT_DAEMON_PORT
    else:
        try:
            with open(PORT_FILE, "r") as f:
                port = int(f.read().strip())
                _logger.debug("read_port_from_shm (file): %d", port)
                return port
        except (FileNotFoundError, ValueError, OSError) as e:
            _logger.debug("read_port_from_shm (file): failed %s", e)
            return DEFAULT_DAEMON_PORT


def write_port_to_shm(port: int) -> Optional[mmap.mmap]:
    """将端口号写入命名共享内存

    Args:
        port: 端口号。

    Returns:
        mmap 对象（Windows，调用方必须保持引用，否则共享内存被销毁），
        Unix 返回 None。
    """
    port_bytes = str(port).encode("ascii").ljust(MMAP_SIZE, b"\x00")
    _logger.info("write_port_to_shm: port=%d", port)
    if IS_WINDOWS:
        shm = mmap.mmap(-1, MMAP_SIZE, tagname=MMAP_NAME)
        shm.write(port_bytes)
        return shm
    else:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PORT_FILE, "w") as f:
            f.write(str(port))
        return None


def cleanup_port_shm():
    """清理共享内存残留（Unix 端口文件）"""
    if not IS_WINDOWS:
        try:
            if os.path.exists(PORT_FILE):
                os.remove(PORT_FILE)
        except OSError:
            pass


def write_pid_file(pid: int, port: int):
    """将守护进程 PID 和端口写入 PID 文件

    PID 文件格式：第一行 PID，第二行端口。用于单实例检查和孤儿清理。

    Args:
        pid: 守护进程 PID。
        port: 守护进程监听端口。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(PID_FILE, "w") as f:
            f.write(f"{pid}\n{port}\n")
        _logger.info("write_pid_file: pid=%d port=%d", pid, port)
    except OSError as e:
        _logger.warning("write_pid_file failed: %s", e)


def read_pid_file() -> Optional[tuple]:
    """从 PID 文件读取守护进程 PID 和端口

    Returns:
        (pid, port) 元组，读取失败或文件不存在返回 None。
    """
    try:
        with open(PID_FILE, "r") as f:
            lines = f.read().strip().split("\n")
            pid = int(lines[0].strip())
            port = int(lines[1].strip()) if len(lines) > 1 else DEFAULT_DAEMON_PORT
            _logger.debug("read_pid_file: pid=%d port=%d", pid, port)
            return (pid, port)
    except (FileNotFoundError, ValueError, IndexError, OSError) as e:
        _logger.debug("read_pid_file: failed %s", e)
        return None


def cleanup_pid_file():
    """清理 PID 文件"""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except OSError:
        pass
