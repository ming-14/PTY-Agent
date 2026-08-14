"""进程间通信子包 — 共享内存 IPC + 单实例锁

统一承载守护进程与客户端之间的跨进程通信原语：
- 共享内存读写（认证令牌、HMAC 密钥；daemon 端口为固定端口配置，不在 SHM）
- 单实例锁（single_instance.py）：Windows 命名互斥 / Unix flock
"""

from .shm import (
    cleanup_all_shm,
    cleanup_auth_shm,
    cleanup_hmac_shm,
    generate_auth_token,
    read_auth_token,
    read_hmac_key,
    write_auth_token,
    write_hmac_key,
)

__all__ = [
    "cleanup_all_shm",
    "cleanup_auth_shm",
    "cleanup_hmac_shm",
    "generate_auth_token",
    "read_auth_token",
    "read_hmac_key",
    "write_auth_token",
    "write_hmac_key",
]
