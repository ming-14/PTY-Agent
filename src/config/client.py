"""客户端配置 —— 仅 Client 进程使用的常量

来源: common.toml + shared.toml + client/client.toml + 运行时计算属性
包含共有配置（common）与共享配置（shared）的所有常量，可直接从此模块导入。
"""

import os

from . import common as _common
from ._loader import flatten, load_toml, merge

_all = merge(
    flatten(load_toml("common.toml")),
    flatten(load_toml("shared.toml")),
    flatten(load_toml("client.toml", "client")),
)

_all["IS_WINDOWS"] = _common.IS_WINDOWS
_all["DATA_DIR"] = _common.DATA_DIR
_all["PROJECT_ROOT"] = _common.PROJECT_ROOT
_all["LOG_DIR"] = os.path.join(_common.DATA_DIR, "logs")

globals().update(_all)
__all__ = list(_all.keys())
