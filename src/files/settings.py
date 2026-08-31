"""files 文件工具设置 —— 运行时设置持有器

默认值常量（原 plugin.json config.defaults 内化而来）直接在本模块声明，
单一事实来源；运行期可直接改模块级 settings 实例覆盖（如测试强制降级 rg）。
传输协议参数（TRANSFER_*）由核心 src/config/transfer.py 提供。
"""

import os
import shutil
from typing import Optional

from src.config.common import IS_WINDOWS, PROJECT_ROOT

# 搜索忽略目录默认清单（原 plugin.json config.defaults.ignored_dirs 内化）
_DEFAULT_IGNORED_DIRS = (
    ".git",
    ".opencode",
    ".idea",
    ".vscode",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "__pycache__",
    "bin",
    "obj",
    "out",
    "coverage",
    "logs",
    "generated",
)


class Settings:
    """文件工具运行参数（构造即含默认值）"""

    def __init__(
        self,
        max_read_size: int = 262144,
        default_read_limit: int = 2000,
        max_line_length: int = 2000,
        max_path_len: int = 4096,
        max_content_len: int = 1048576,
        max_grep_matches: int = 100,
        max_glob_files: int = 100,
        rg_exe: str = "",
        ignored_dirs=None,
    ):
        self.max_read_size = max_read_size
        self.default_read_limit = default_read_limit
        self.max_line_length = max_line_length
        self.max_path_len = max_path_len
        self.max_content_len = max_content_len
        self.max_grep_matches = max_grep_matches
        self.max_glob_files = max_glob_files
        self.rg_exe = self._resolve_rg(rg_exe)
        self.ignored_dirs = tuple(ignored_dirs if ignored_dirs is not None else _DEFAULT_IGNORED_DIRS)

    @staticmethod
    def _resolve_rg(configured: str) -> Optional[str]:
        """rg 可执行文件：配置值优先，空则自动探测项目根 bin/rg/ 与 PATH

        平台后缀差异：Windows 携带 rg.exe，Unix 携带 rg（无扩展名）。
        """
        if configured:
            from ..config._loader import expand_env

            return expand_env(configured)
        rg_name = "rg.exe" if IS_WINDOWS else "rg"
        project_rg = os.path.join(PROJECT_ROOT, "bin", "rg", rg_name)
        return project_rg if os.path.isfile(project_rg) else shutil.which("rg")


# daemon 级默认实例（常驻共享，状态跨连接）
settings = Settings()


__all__ = ["Settings", "settings"]
