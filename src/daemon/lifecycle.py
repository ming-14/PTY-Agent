"""守护进程层 — 入口与日志配置（守护进程侧）

客户端侧的守护进程管控（start/stop/is_running/客户端日志）见
client/controller.py，本模块只承载守护进程自身的入口逻辑：

- main()                 守护进程主入口
- _setup_logging()       文件日志（UTF-8，RotatingFileHandler 限体积），无控制台输出
- _hide_console_window() Windows 隐藏控制台窗口
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler

from ..config import (
    LOG_DIR,
    LOG_MAX_BYTES,
    LOG_BACKUP_COUNT,
    DAEMON_LOG_LEVEL,
    MANAGED_LOGGERS,
)
from ..protocol.daemon_utils import cleanup_shm_resources

_logger = logging.getLogger("pty-daemon")


def _safe_print(text: str):
    """安全打印 UTF-8 文本到 stdout

    与 client/input.py 的 safe_print 同构，但守护进程是独立进程、
    且不得 import client 层，故保留这份进程内自有的最小实现。
    """
    try:
        sys.stdout.buffer.write(text.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
    except Exception:
        pass


def _hide_console_window():
    """隐藏当前进程的控制台窗口（Windows）"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


def _setup_logging():
    """配置日志：仅文件输出（UTF-8），无控制台输出

    守护进程是单一长驻写入者，因此用 RotatingFileHandler 控制体积
    （LOG_MAX_BYTES × (1 + LOG_BACKUP_COUNT)）。
    """
    level_name = DAEMON_LOG_LEVEL
    if level_name is None:
        for name in MANAGED_LOGGERS:
            logger = logging.getLogger(name)
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.setLevel(logging.WARNING)
            logger.propagate = False
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "daemon.log")
    fh = RotatingFileHandler(log_file, encoding="utf-8", mode="a",
                             maxBytes=LOG_MAX_BYTES,
                             backupCount=LOG_BACKUP_COUNT)
    fh.setFormatter(logging.Formatter(
        "[pty-agent:daemon] %(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    level = getattr(logging, level_name.upper(), logging.DEBUG)
    for name in MANAGED_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(fh)
        logger.setLevel(level)
        logger.propagate = False


def main():
    """守护进程入口

    通过共享内存发布 PID + 状态 + 心跳，启动信箱轮询服务器。
    无端口参数、无 socket。
    """
    _hide_console_window()
    _setup_logging()
    _logger.info("=== 守护进程启动 ===")

    _logger.info("PID: %s", os.getpid())

    try:
        from ..backend.subprocess import format_shell_info
        _logger.info(format_shell_info())
    except Exception:
        pass

    from .server import DaemonServer

    server = DaemonServer()
    try:
        server.run()
    except OSError as e:
        _logger.error("守护进程启动失败: %s", e)
        _safe_print(f"[pty-agent] 守护进程启动失败: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        _logger.info("收到键盘中断，关闭守护进程...")
    finally:
        cleanup_shm_resources()
        server.stop()


if __name__ == "__main__":
    main()
