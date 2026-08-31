"""日志系统：统一的 logger 配置。

parser 作为库被 daemon/CLI 进程内嵌加载，默认**静默**（NullHandler）——
否则 CLI 侧读消息时 INFO/WARNING 日志会泄露到客户端 stderr 污染输出。
需要调试时设置 <PARSER>_LOG_LEVEL 环境变量（如 DEVINPARSER_LOG_LEVEL=DEBUG）
显式开启输出到 stderr。
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
    root = logging.getLogger("devinparser")
    level_name = os.environ.get("DEVINPARSER_LOG_LEVEL", "").upper()
    if level_name:
        # 显式开启：输出到 stderr
        level = getattr(logging, level_name, logging.INFO)
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.setLevel(level)
        root.addHandler(handler)
        root.propagate = False
    else:
        # 默认静默（嵌入宿主进程时不污染宿主输出）
        root.addHandler(logging.NullHandler())
        root.setLevel(logging.CRITICAL)
        root.propagate = False
    _initialized = True


def get_logger(name: str = "devinparser") -> logging.Logger:
    """获取统一配置的 logger。"""
    _init_root()
    if name == "devinparser" or name.startswith("devinparser."):
        return logging.getLogger(name)
    return logging.getLogger(f"devinparser.{name}")
