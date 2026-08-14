"""客户端侧 — 客户端日志配置

守护进程的启动/停止/探测（start_daemon / stop_daemon / is_running / 端口发现）
属 client 侧控制能力，已独立为 src/daemonctl 包；daemon 自身入口在 src/daemon/lifecycle.py。
本模块仅保留客户端日志配置（setup_client_logging）。
"""

import logging

from ..config.client import CLIENT_LOG_LEVEL, CLIENT_LOGGERS
from ..config.shared import LOG_DATE_FORMAT, LOG_DIR, LOG_FORMAT


def setup_client_logging():
    """客户端日志配置：写入 <用户目录>/.pty-agent/logs/client-{时间戳}.log

    为 pty-client / pty-daemonctl 等前台相关 logger 配置文件输出。
    CLIENT_LOG_LEVEL 设为 None 则不配置日志。
    """
    if CLIENT_LOG_LEVEL is None:
        return
    from ..logging_setup import configure_log_files

    level = getattr(logging, CLIENT_LOG_LEVEL.upper(), logging.DEBUG)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    configure_log_files(
        LOG_DIR, {"client": CLIENT_LOGGERS}, {"client": level}, formatter
    )
