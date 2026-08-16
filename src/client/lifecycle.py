"""客户端侧 — 客户端日志配置

守护进程的启动/停止/探测（start_daemon / stop_daemon / is_running / 端口发现）
属 client 侧控制能力，已独立为 src/daemonctl 包；daemon 自身入口在 src/daemon/lifecycle.py。
本模块仅保留客户端日志配置（setup_client_logging），委托给 src/logging 子包。
"""

from ..logging import setup_client_logging

__all__ = ["setup_client_logging"]
