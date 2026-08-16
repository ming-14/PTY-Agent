"""客户端配置 —— 仅 Client 进程使用的常量

来源: common.toml + shared.toml + logging.toml + client/client.toml + client/logging.toml + 运行时计算属性
包含共有配置（common）与共享配置（shared）的所有常量，可直接从此模块导入。
日志跨侧共享配置（格式/归档/异步队列）来自 logging.toml，侧专属（级别/分组）来自 client/logging.toml。
"""

import os

from . import common as _common
from ._loader import flatten, load_toml, merge

_all = merge(
    flatten(load_toml("common.toml")),
    flatten(load_toml("shared.toml")),
    flatten(load_toml("logging.toml")),
    flatten(load_toml("client.toml", "client")),
    flatten(load_toml("logging.toml", "client")),
)

_all["IS_WINDOWS"] = _common.IS_WINDOWS
_all["DATA_DIR"] = _common.DATA_DIR
_all["PROJECT_ROOT"] = _common.PROJECT_ROOT
_all["LOG_DIR"] = os.path.join(_common.DATA_DIR, "logs")

globals().update(_all)
__all__ = list(_all.keys())
