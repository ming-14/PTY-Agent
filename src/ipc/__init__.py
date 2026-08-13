"""进程间通信子包 — 共享内存 IPC + 单实例锁

统一承载守护进程与客户端之间的跨进程通信原语：
- 共享内存读写（端口/PID、认证令牌、HMAC 密钥）
- 单实例锁（single_instance.py）：Windows 命名互斥 / Unix flock
"""

from .shm import (
    read_daemon_info_from_shm,
    read_port_from_shm,
    write_daemon_info_to_shm,
    cleanup_port_shm,
    generate_auth_token,
    read_auth_token,
    write_auth_token,
    cleanup_auth_shm,
    read_hmac_key,
    write_hmac_key,
    cleanup_hmac_shm,
    cleanup_all_shm,
)

__all__ = [
    "read_daemon_info_from_shm",
    "read_port_from_shm",
    "write_daemon_info_to_shm",
    "cleanup_port_shm",
    "generate_auth_token",
    "read_auth_token",
    "write_auth_token",
    "cleanup_auth_shm",
    "read_hmac_key",
    "write_hmac_key",
    "cleanup_hmac_shm",
    "cleanup_all_shm",
]
