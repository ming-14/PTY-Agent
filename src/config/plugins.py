"""插件系统配置 —— Daemon 进程专用

来源: config/plugins/plugins.json + 运行时计算属性
JSON 显式指定插件位置（相对项目根的路径），不再扫描目录:

    {
        "enabled": true,
        "plugins": ["config/plugins/state_check.py"]
    }

导出:
- ENABLED:     插件系统总开关
- PLUGIN_PATHS: 实际加载的插件位置（绝对路径，JSON + 环境变量追加）
"""

import json
import os

from . import common as _common

_PLUGINS_JSON = os.path.join(_common.PROJECT_ROOT, "config", "plugins", "plugins.json")


def _load_plugins_json() -> dict:
    with open(_PLUGINS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"plugins.json 顶层必须为对象: {_PLUGINS_JSON}")
    return data


_config = _load_plugins_json()

# JSON 中显式指定的插件位置（相对项目根展开为绝对路径）
_json_paths = [
    os.path.join(_common.PROJECT_ROOT, p)
    for p in (_config.get("plugins") or [])
    if isinstance(p, str) and p
]

# 环境变量 PTY_PLUGIN_DIRS（os.pathsep 分隔）追加额外位置，供部署/测试隔离
_env_paths = [p for p in os.environ.get("PTY_PLUGIN_DIRS", "").split(os.pathsep) if p]

ENABLED = bool(_config.get("enabled", True))
PLUGIN_PATHS = _json_paths + _env_paths

__all__ = ["ENABLED", "PLUGIN_PATHS"]
