"""daemon 控制包 —— client 侧守护进程生命周期控制与 TLS 连接

与 daemon 核心完全解耦：本包仅依赖共享层（config / ipc / protocol / auth / common），
不 import src.daemon / src.session / src.pty / src.process 等 daemon 侧模块。

公共接口：
- start_daemon / stop_daemon / is_running   启动、停止、探测（固定端口 + 单实例锁）
- _find_daemon_port / _find_daemon_pid     端口/PID 发现（e2e 测试使用）
- _stop_daemon_force / _cleanup_credentials 强制清理（e2e 测试使用）
- TLSClient                                TLS 连接 + TOFU 验证（pubkey 跨机模式）
"""

from .lifecycle import (
    _cleanup_credentials,
    _find_daemon_pid,
    _find_daemon_port,
    _force_kill_pid,
    _ping_daemon,
    _stop_daemon_force,
    is_running,
    start_daemon,
    stop_daemon,
)
from .tls import TLSClient

__all__ = [
    "TLSClient",
    "_cleanup_credentials",
    "_find_daemon_pid",
    "_find_daemon_port",
    "_force_kill_pid",
    "_ping_daemon",
    "_stop_daemon_force",
    "is_running",
    "start_daemon",
    "stop_daemon",
]
