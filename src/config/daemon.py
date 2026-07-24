"""守护进程配置 —— 仅 Daemon 进程使用的常量

来源: common.toml + daemon.toml + logging.toml + web.toml + 运行时计算属性
包含共有配置（common）的所有常量，可直接从此模块导入。
"""

import os

from ._loader import load_toml, flatten, merge
from . import common as _common

_all = merge(
    flatten(load_toml("common.toml")),
    flatten(load_toml("daemon.toml")),
    flatten(load_toml("logging.toml")),
    flatten(load_toml("web.toml")),
)

_all["IS_WINDOWS"] = _common.IS_WINDOWS
_all["DATA_DIR"] = _common.DATA_DIR
_all["PROJECT_ROOT"] = _common.PROJECT_ROOT
_all["PORT_FILE"] = os.path.join(_common.DATA_DIR, "daemon.port")
_all["LOG_DIR"] = os.path.join(_common.PROJECT_ROOT, "logs")

globals().update(_all)
__all__ = list(_all.keys())
