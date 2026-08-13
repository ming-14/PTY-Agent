"""src/files 包 —— 文件工具业务（file read/write/edit/grep/glob）

结构（按工具域分组）：
- 根级：公共模块（errors/paths/state/history/diff/permission）
- read/：file read 用例
- write/：file write / edit 用例
- search/：file grep / glob 用例与忽略过滤

handler 通过子包或本层聚合导出调用，业务全部在此包实现。
"""

from .errors import (
    FileToolError,
    FileReadRequiredError,
    FilePermissionDeniedError,
)
from .state import FileRecordStore, get_default_store
from .paths import (
    resolve_session_path,
    is_within,
    has_git_bash_style_path,
    GIT_BASH_PATH_HINT,
)
from .read import read_file
from .write import write_file, edit_file
from .search import grep_files, glob_files, is_ignored

__all__ = [
    "FileToolError",
    "FileReadRequiredError",
    "FilePermissionDeniedError",
    "FileRecordStore",
    "get_default_store",
    "resolve_session_path",
    "is_within",
    "has_git_bash_style_path",
    "GIT_BASH_PATH_HINT",
    "read_file",
    "write_file",
    "edit_file",
    "grep_files",
    "glob_files",
    "is_ignored",
]