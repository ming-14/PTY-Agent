"""日志系统：统一的 logger 配置。

parser 作为库被 daemon/CLI 进程内嵌加载，默认**静默**（NullHandler）——
否则 CLI 侧读消息时 INFO/WARNING 日志会泄露到客户端 stderr 污染输出。
需要调试时设置 smartparser_LOG_LEVEL 环境变量（如 =DEBUG）显式开启输出到 stderr。
"""
from __future__ import annotations

import logging
import os
import sys

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"

_initialized = False


def _init_root() -> None:
    global _initialized
    if _initialized:
        return
    root = logging.getLogger("smartparser")
    level_name = os.environ.get("smartparser_LOG_LEVEL", "").upper()
    if level_name:
        level = getattr(logging, level_name, logging.INFO)
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.setLevel(level)
        root.addHandler(handler)
        root.propagate = False
    else:
        root.addHandler(logging.NullHandler())
        root.setLevel(logging.CRITICAL)
        root.propagate = False
    _initialized = True


def get_logger(name: str = "smartparser") -> logging.Logger:
    """获取统一配置的 logger。"""
    _init_root()
    if name == "smartparser" or name.startswith("smartparser."):
        return logging.getLogger(name)
    return logging.getLogger(f"smartparser.{name}")