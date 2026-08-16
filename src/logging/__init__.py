"""高性能日志系统 — 异步队列 + 上下文绑定 + logger 注册表

公开 API:
    get_logger(name)       获取 logger（校验注册表）
    register(name, group)  注册 logger 名
    bind(**kwargs)         绑定上下文字段（session_id 等）
    unbind(token)          解除绑定
    get_context()          获取当前上下文
    setup_daemon_logging() daemon 侧装配
    setup_client_logging() client 侧装配
    shutdown()             优雅关闭（刷队列 + 最后归档）
"""

from . import registry
from .context import bind, clear, get_context, unbind
from .registry import get_logger, register_group
from .setup import setup_client_logging, setup_daemon_logging, shutdown

__all__ = [
    "bind",
    "clear",
    "get_context",
    "get_logger",
    "register_group",
    "registry",
    "setup_client_logging",
    "setup_daemon_logging",
    "shutdown",
    "unbind",
]
