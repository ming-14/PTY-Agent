"""守护进程子包 — 生命周期管理、TCP 服务器与请求处理"""

from .lifecycle import is_running, start_daemon, stop_daemon
from .server import DaemonServer
from .handler import RequestHandler

__all__ = ["is_running", "start_daemon", "stop_daemon", "DaemonServer", "RequestHandler"]
