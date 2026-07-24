"""进程间通信子包 — 共享内存 IPC

统一管理守护进程与客户端之间的所有进程间通信：
- 共享内存读写（端口/PID、认证令牌、HMAC 密钥）

HMAC 签名功能已迁移至 src.auth.hmac_signer + src.protocol.message.Message.set_outbound_signer()。
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
    load_hmac_key_to_state,
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
    "load_hmac_key_to_state",
]
