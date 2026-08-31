"""file 文件工具 —— 主程序内置功能（原 files 功能内化）

进程级文件工具：read/write/edit/grep/glob/upload/download。
消息由内置 daemon handler（src/daemon/handlers/file_handler.py）接管，
不再经插件系统路由；本包只含业务实现，按工具域分包：
- 根级：公共模块（errors / paths / state / history / diff / permission / settings）
- read/：file read 用例
- write/：file write / edit 用例
- search/：file grep / glob 用例与忽略过滤
- transfer/：upload / download 传输（判定 / 映射 / daemon 侧帧协议）
"""

from .errors import (
    FilePermissionDeniedError,
    FileReadRequiredError,
    FileToolError,
)
from .paths import is_within, normalize_key, resolve_session_path
from .state import FileRecordStore, get_default_store
from .history import FileHistoryStore
from .diff import generate_diff
from .permission import PermissionPolicy

# 注：不在此处导出 settings/Settings —— 会遮蔽同名的 src.files.settings 子模块
# （import src.files.settings as _s 语义依赖包属性指向模块），
# 消费方一律用 from src.files.settings import settings 全限定导入。

__all__ = [
    "FileToolError",
    "FileReadRequiredError",
    "FilePermissionDeniedError",
    "is_within",
    "normalize_key",
    "resolve_session_path",
    "FileRecordStore",
    "get_default_store",
    "FileHistoryStore",
    "generate_diff",
    "PermissionPolicy",
]