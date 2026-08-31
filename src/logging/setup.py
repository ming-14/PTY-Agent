"""日志装配入口 — daemon 侧与 client 侧的日志系统初始化

装配流程：
1. 从 config 常量组装 LoggingConfig
2. 注册表注册所有 logger 名
3. 创建文件 handler + 异步队列路由
4. 各 logger 挂 AsyncQueueHandler，启动后台监听线程
5. daemon 侧额外启动归档线程

级别为 None 时挂 NullHandler 静默。所有 logger propagate=False。
"""

import logging
import os
import sys
from typing import Dict, Optional

from . import registry
from ._queue import AsyncLogDispatcher
from .archiver import LogArchiver
from .config import LoggingConfig
from .formatters import ContextFormatter
from .handlers import create_file_handler, generate_log_timestamp

# 模块级单例，供 shutdown() 使用
_dispatcher: Optional[AsyncLogDispatcher] = None
_archiver: Optional[LogArchiver] = None


def _assemble(config: LoggingConfig, console: bool = False) -> Dict[str, str]:
    """按配置装配日志系统，返回 {分组名: 日志文件路径}

    Args:
        console: 同时向 stderr 输出（前台运行 / 服务监督器场景，供 s6-log 等捕获）。
    """
    global _dispatcher

    os.makedirs(config.log_dir, exist_ok=True)
    timestamp = generate_log_timestamp()

    # 注册所有 logger 名到注册表
    for group, names in config.groups.items():
        registry.register_group(group, names)
    registry.mark_config_loaded()

    # 全部级别为 None 时静默：所有 logger 挂 NullHandler
    if not any(lv is not None for lv in config.levels.values()):
        for names in config.groups.values():
            for name in names:
                logger = logging.getLogger(name)
                logger.handlers.clear()
                logger.addHandler(logging.NullHandler())
                logger.setLevel(logging.WARNING)
                logger.propagate = False
        return {}

    formatter = ContextFormatter(config.log_format, datefmt=config.log_date_format)
    _dispatcher = AsyncLogDispatcher(queue_size=config.queue_size)
    queue_handler = _dispatcher.create_queue_handler()
    # 前台模式：额外向 stderr 输出（监督器日志捕获），与文件 handler 同一 formatter
    console_handler = logging.StreamHandler(sys.stderr) if console else None
    if console_handler is not None:
        console_handler.setFormatter(formatter)

    files: Dict[str, str] = {}
    for group, names in config.groups.items():
        level = config.levels.get(group)
        if level is None or not names:
            continue
        log_file = os.path.join(config.log_dir, f"{group}-{timestamp}.log")
        fh = create_file_handler(log_file, formatter, level)
        _dispatcher.add_route(names, fh)

        for name in names:
            logger = logging.getLogger(name)
            logger.handlers.clear()
            logger.addHandler(queue_handler)
            if console_handler is not None:
                logger.addHandler(console_handler)
            logger.setLevel(level)
            logger.propagate = False
        files[group] = log_file

    _dispatcher.start()
    return files


def setup_daemon_logging(console: bool = False) -> Dict[str, str]:
    """daemon 侧装配：8 分组 → 异步队列 → 文件 + 启动归档线程

    分组与级别映射：
    - daemon/session/pty/protocol/auth/sandbox → DAEMON_LOG_LEVEL
    - web/screenshare → WEB_LOG_LEVEL

    Args:
        console: 同时向 stderr 输出（前台运行 / 服务监督器场景）。

    Returns:
        {分组名: 日志文件路径}
    """
    global _archiver

    from ..config.daemon import (
        AUTH_LOGGERS,
        DAEMON_LOG_LEVEL,
        DAEMON_LOGGERS,
        LOG_ARCHIVE_INTERVAL,
        LOG_DATE_FORMAT,
        LOG_DIR,
        LOG_FORMAT,
        LOG_QUEUE_SIZE,
        PROTOCOL_LOGGERS,
        PTY_LOGGERS,
        SANDBOX_LOGGERS,
        SCREENSHARE_LOGGERS,
        SESSION_LOGGERS,
        WEB_LOG_LEVEL,
        WEB_LOGGERS,
    )

    daemon_level = (
        getattr(logging, DAEMON_LOG_LEVEL.upper(), logging.DEBUG)
        if DAEMON_LOG_LEVEL
        else None
    )
    web_level = (
        getattr(logging, WEB_LOG_LEVEL.upper(), logging.DEBUG)
        if WEB_LOG_LEVEL
        else None
    )

    groups = {
        "daemon": DAEMON_LOGGERS,
        "session": SESSION_LOGGERS,
        "pty": PTY_LOGGERS,
        "protocol": PROTOCOL_LOGGERS,
        "auth": AUTH_LOGGERS,
        "sandbox": SANDBOX_LOGGERS,
        "web": WEB_LOGGERS,
        "screenshare": SCREENSHARE_LOGGERS,
    }
    levels = {
        g: daemon_level
        for g in ("daemon", "session", "pty", "protocol", "auth", "sandbox")
    }
    levels.update({g: web_level for g in ("web", "screenshare")})

    config = LoggingConfig(
        log_dir=LOG_DIR,
        log_format=LOG_FORMAT,
        log_date_format=LOG_DATE_FORMAT,
        groups=groups,
        levels=levels,
        archive_interval=LOG_ARCHIVE_INTERVAL,
        queue_size=LOG_QUEUE_SIZE,
    )
    files = _assemble(config, console=console)

    if files:
        _archiver = LogArchiver(LOG_DIR, LOG_ARCHIVE_INTERVAL)
        _archiver.start()

    return files


def setup_client_logging() -> Dict[str, str]:
    """client 侧装配：1 分组(client) → 异步队列 → 文件

    不启动归档线程（daemon 侧负责归档）。

    Returns:
        {分组名: 日志文件路径}
    """
    from ..config.client import CLIENT_LOG_LEVEL, CLIENT_LOGGERS, LOG_QUEUE_SIZE
    from ..config.shared import LOG_DATE_FORMAT, LOG_DIR, LOG_FORMAT

    if CLIENT_LOG_LEVEL is None:
        registry.register_group("client", CLIENT_LOGGERS)
        registry.mark_config_loaded()
        return {}

    level = getattr(logging, CLIENT_LOG_LEVEL.upper(), logging.DEBUG)
    config = LoggingConfig(
        log_dir=LOG_DIR,
        log_format=LOG_FORMAT,
        log_date_format=LOG_DATE_FORMAT,
        groups={"client": CLIENT_LOGGERS},
        levels={"client": level},
        queue_size=LOG_QUEUE_SIZE,
    )
    return _assemble(config)


def shutdown(timeout: float = 5.0) -> None:
    """优雅关闭：刷空日志队列 + 最后一次归档

    在进程退出前调用，确保所有日志已落盘。
    """
    global _dispatcher, _archiver
    if _dispatcher is not None:
        _dispatcher.stop(timeout=timeout)
        _dispatcher = None
    if _archiver is not None:
        _archiver.stop()
        _archiver = None
