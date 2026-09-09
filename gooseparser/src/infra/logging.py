"""日志系统：统一的 logger 配置。

提供 get_logger() 获取带统一格式的 logger。
默认输出到 stderr，级别可通过 GOOSEPARSER_LOG_LEVEL 环境变量调节。
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
    level_name = os.environ.get("GOOSEPARSER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    root = logging.getLogger("gooseparser")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    _initialized = True


def get_logger(name: str = "gooseparser") -> logging.Logger:
    """获取统一配置的 logger。"""
    _init_root()
    if name == "gooseparser" or name.startswith("gooseparser."):
        return logging.getLogger(name)
    return logging.getLogger(f"gooseparser.{name}")
