"""file grep / glob 用例子包 —— 对外导出搜索入口、结果类型与忽略过滤

rg 双引擎（rg 优先，缺失/失败降级纯 Python）共用 ignore 过滤逻辑。
"""

from .glob_ import GlobResult, glob_files
from .grep import GrepMatch, GrepResult, grep_files
from .ignore import is_ignored

__all__ = ["grep_files", "GrepMatch", "GrepResult", "glob_files", "GlobResult", "is_ignored"]
