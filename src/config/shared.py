"""共享配置 —— Daemon 与 Client 两侧均需使用的常量

来源: common.toml + shared.toml + logging.toml + 运行时计算属性
包含共有配置（common）的所有常量，可直接从此模块导入。
日志跨侧共享配置（格式/归档/异步队列）来自 logging.toml。
"""

import os

from . import common as _common
from ._loader import flatten, load_toml, merge

_all = merge(
    flatten(load_toml("common.toml")),
    flatten(load_toml("shared.toml")),
    flatten(load_toml("logging.toml")),
)

_all["IS_WINDOWS"] = _common.IS_WINDOWS
_all["DATA_DIR"] = _common.DATA_DIR
_all["PROJECT_ROOT"] = _common.PROJECT_ROOT
_all["LOG_DIR"] = os.path.join(_common.DATA_DIR, "logs")

globals().update(_all)
__all__ = list(_all.keys())
