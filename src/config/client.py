"""客户端配置 —— 仅 Client 进程使用的常量

来源: common.toml + client.toml + 运行时计算属性
包含共有配置（common）的所有常量，可直接从此模块导入。
"""

from ._loader import load_toml, flatten, merge
from . import common as _common

_all = merge(
    flatten(load_toml("common.toml")),
    flatten(load_toml("client.toml")),
)

_all["IS_WINDOWS"] = _common.IS_WINDOWS
_all["DATA_DIR"] = _common.DATA_DIR
_all["PROJECT_ROOT"] = _common.PROJECT_ROOT

globals().update(_all)
__all__ = list(_all.keys())
