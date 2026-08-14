"""共有配置 —— Daemon 与 Client 均需使用的常量

来源: common.toml + 运行时计算属性
"""

import os
import sys

from ._loader import flatten, load_toml, merge

_all = merge(flatten(load_toml("common.toml")))

_all["IS_WINDOWS"] = sys.platform == "win32"
_all["DATA_DIR"] = os.path.join(os.path.expanduser("~"), ".pty-agent")
_all["PROJECT_ROOT"] = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

globals().update(_all)
__all__ = list(_all.keys())
