"""files 插件设置 —— 运行时设置持有器

由插件 on_init 从 ctx.config（plugin.json 默认 + config.yaml + 环境变量，
经 config.schema.json 校验）注入；各用例模块经本模块读取设置值。

模块级默认实例以同目录 plugin.json 的 config.defaults 初始化（配置单一事实
来源，避免默认值双写）。传输协议参数（TRANSFER_*）仍由核心
src/config/transfer.py 提供。
"""

import json
import os
import shutil
from typing import List, Optional

from src.config.common import IS_WINDOWS, PROJECT_ROOT

_SETTING_KEYS = (
    "max_read_size",
    "default_read_limit",
    "max_line_length",
    "max_path_len",
    "max_content_len",
    "max_grep_matches",
    "max_glob_files",
    "rg_exe",
    "ignored_dirs",
)


class Settings:
    """文件工具运行参数（构造即含默认值，插件启动时 apply 覆盖）"""

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
        ignored_dirs: Optional[List[str]] = None,
    ):
        self.max_read_size = max_read_size
        self.default_read_limit = default_read_limit
        self.max_line_length = max_line_length
        self.max_path_len = max_path_len
        self.max_content_len = max_content_len
        self.max_grep_matches = max_grep_matches
        self.max_glob_files = max_glob_files
        self.rg_exe = self._resolve_rg(rg_exe)
        self.ignored_dirs = tuple(ignored_dirs or [])

    @staticmethod
    def _resolve_rg(configured: str) -> Optional[str]:
        """rg 可执行文件：配置值优先，空则自动探测项目根 bin/rg/ 与 PATH

        平台后缀差异：Windows 携带 rg.exe，Unix 携带 rg（无扩展名）。
        """
        if configured:
            return os.path.expandvars(os.path.expanduser(configured))
        rg_name = "rg.exe" if IS_WINDOWS else "rg"
        project_rg = os.path.join(PROJECT_ROOT, "bin", "rg", rg_name)
        return project_rg if os.path.isfile(project_rg) else shutil.which("rg")


def _load_manifest_defaults() -> dict:
    """读取同目录 plugin.json 的 config.defaults（配置单一事实来源）"""
    manifest_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin.json")
    try:
        with open(manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        defaults = data.get("config", {}).get("defaults", {})
        if isinstance(defaults, dict):
            return {k: v for k, v in defaults.items() if k in _SETTING_KEYS}
    except (OSError, ValueError):
        pass
    return {}


# 进程级默认实例：以 plugin.json 默认值初始化（插件 on_init 覆盖为运行配置）
settings = Settings(**_load_manifest_defaults())


def apply(values: dict) -> None:
    """从插件配置视图注入运行设置（on_init 调用一次）"""
    for key in (
        "max_read_size",
        "default_read_limit",
        "max_line_length",
        "max_path_len",
        "max_content_len",
        "max_grep_matches",
        "max_glob_files",
        "rg_exe",
        "ignored_dirs",
    ):
        if key in values:
            setattr(settings, key, values[key])
    settings.ignored_dirs = tuple(settings.ignored_dirs or ())
    settings.rg_exe = Settings._resolve_rg(settings.rg_exe or "")


__all__ = ["Settings", "apply", "settings"]