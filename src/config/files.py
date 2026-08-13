"""文件工具配置 —— 加载 files.toml 供 src/files/ 使用

来源: files.toml + 运行时属性（RG_EXE 自动探测）
"""

import os
import shutil

from ._loader import load_toml
from . import common as _common

_cfg = load_toml("files.toml")["files"]

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

# 忽略目录清单（src/files/search/ignore.py 消费）
IGNORED_DIRS = tuple(_cfg["IGNORED_DIRS"])

# 文件传输（file upload/download，src/files/transfer/ 消费）
TRANSFER_CHUNK_SIZE = int(_cfg["TRANSFER_CHUNK_SIZE"])
TRANSFER_MAX_FILES = int(_cfg["TRANSFER_MAX_FILES"])
TRANSFER_MAX_CONTROL = int(_cfg["TRANSFER_MAX_CONTROL"])
TRANSFER_MAX_SIZE = int(_cfg["TRANSFER_MAX_SIZE"])
TRANSFER_TMP_SUFFIX = str(_cfg["TRANSFER_TMP_SUFFIX"])
TRANSFER_PROGRESS_INTERVAL = float(_cfg["TRANSFER_PROGRESS_INTERVAL"])
TRANSFER_TIMEOUT = float(_cfg["TRANSFER_TIMEOUT"])

# rg 可执行文件：配置值优先，空则自动探测项目根 bin/rg/ 与 PATH
_RG_CONFIGURED = _cfg["RG_EXE"]
if _RG_CONFIGURED:
    RG_EXE = os.path.expandvars(os.path.expanduser(_RG_CONFIGURED))
else:
    _project_rg = os.path.join(_common.PROJECT_ROOT, "bin", "rg", "rg.exe")
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
    "TRANSFER_CHUNK_SIZE",
    "TRANSFER_MAX_FILES",
    "TRANSFER_MAX_CONTROL",
    "TRANSFER_MAX_SIZE",
    "TRANSFER_TMP_SUFFIX",
    "TRANSFER_PROGRESS_INTERVAL",
    "TRANSFER_TIMEOUT",
]