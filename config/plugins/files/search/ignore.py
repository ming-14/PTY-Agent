"""搜索忽略过滤 —— 隐藏文件与常见构建/依赖目录

路径任意段命中忽略清单（含隐藏段）即跳过。
被 file grep / file glob 双引擎（rg 与降级）共同用于降级路径；
rg 引擎自身尊重 .gitignore，忽略清单仅在降级时生效。
"""

import os
import re

from config.plugins.files.settings import settings

_SEGMENT_SPLIT = re.compile(r"[\\/]")


def is_ignored(path: str) -> bool:
    """路径是否应被搜索忽略（隐藏文件或命中忽略目录清单）

    忽略清单经 settings 惰性读取（插件 on_init 时注入配置值）。
    """
    for part in _SEGMENT_SPLIT.split(path):
        if part.startswith("."):
            return True
        if part in settings.ignored_dirs:
            return True
    return False