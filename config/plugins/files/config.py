"""files 插件配置 —— 加载本目录 files.toml（插件自治，不依赖核心配置域）

导出文件工具业务参数：读/写/搜索限制、忽略目录清单、RG_EXE（探测）。
传输协议参数（TRANSFER_*）不在此：它们是 daemon-CLI 两端通信契约，
由核心 src/config/transfer.py 提供，本插件经 src.config.transfer 引用。
"""

import os
import shutil

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from src.config.common import PROJECT_ROOT

# 插件目录内 files.toml：基于 __file__ 定位（与运行 cwd 无关）
_FILES_TOML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files.toml")

with open(_FILES_TOML, "rb") as f:
    _cfg = tomllib.load(f)["files"]

# 读限制
MAX_READ_SIZE = _cfg["MAX_READ_SIZE"]
DEFAULT_READ_LIMIT = _cfg["DEFAULT_READ_LIMIT"]
MAX_LINE_LENGTH = _cfg["MAX_LINE_LENGTH"]

# 参数长度上限
MAX_PATH_LEN = _cfg["MAX_PATH_LEN"]
MAX_CONTENT_LEN = _cfg["MAX_CONTENT_LEN"]

# 搜索限制
MAX_GREP_MATCHES = _cfg["MAX_GREP_MATCHES"]
MAX_GLOB_FILES = _cfg["MAX_GLOB_FILES"]

# 忽略目录清单
IGNORED_DIRS = tuple(_cfg["IGNORED_DIRS"])

# rg 可执行文件：配置值优先，空则自动探测项目根 bin/rg/ 与 PATH
_RG_CONFIGURED = _cfg["RG_EXE"]
if _RG_CONFIGURED:
    RG_EXE = os.path.expandvars(os.path.expanduser(_RG_CONFIGURED))
else:
    _project_rg = os.path.join(PROJECT_ROOT, "bin", "rg", "rg.exe")
    RG_EXE = _project_rg if os.path.isfile(_project_rg) else shutil.which("rg")

__all__ = [
    "MAX_READ_SIZE",
    "DEFAULT_READ_LIMIT",
    "MAX_LINE_LENGTH",
    "MAX_PATH_LEN",
    "MAX_CONTENT_LEN",
    "MAX_GREP_MATCHES",
    "MAX_GLOB_FILES",
    "IGNORED_DIRS",
    "RG_EXE",
]