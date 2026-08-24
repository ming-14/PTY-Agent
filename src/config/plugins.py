"""插件系统配置 —— 目录发现（plugin.json）+ registry.json 状态 + 权限策略

插件发现：扫描 config/plugins/ 下含 plugin.json 的目录（每目录一插件），
环境变量 PTY_PLUGIN_DIRS（os.pathsep 分隔）追加插件目录（须含 plugin.json）。
状态：config/plugins/registry.json —— 总开关 + 各插件启用状态。
策略：config/plugins/policy.json —— 按插件 id 追加授予/拒绝权限（可选）。

导出:
- ENABLED:          插件系统总开关
- PLUGIN_DIRS:      发现的插件目录（绝对路径）
- PLUGIN_STATES:    各插件启用状态映射（registry.json，缺失默认 True）
- POLICY:           权限策略（{"<id>": {"grant": [...], "deny": [...]}}）
- PluginStateStore: registry.json 读写（enable/disable 持久化）
"""

import json
import os
import threading

from . import common as _common

PLUGINS_ROOT = os.path.join(_common.PROJECT_ROOT, "config", "plugins")
_REGISTRY_JSON = os.path.join(PLUGINS_ROOT, "registry.json")
_POLICY_JSON = os.path.join(PLUGINS_ROOT, "policy.json")

_MANIFEST_FILE = "plugin.json"


def _load_json(path: str, default):
    """读取 JSON 对象文件；缺失/非法返回 default"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else default
    except (OSError, ValueError):
        return default


def _discover_plugin_dirs() -> list:
    """扫描插件根目录与 PTY_PLUGIN_DIRS：含 plugin.json 的目录即插件目录"""
    dirs = []
    try:
        for name in sorted(os.listdir(PLUGINS_ROOT)):
            candidate = os.path.join(PLUGINS_ROOT, name)
            if os.path.isdir(candidate) and os.path.isfile(
                os.path.join(candidate, _MANIFEST_FILE)
            ):
                dirs.append(candidate)
    except OSError:
        pass
    for d in os.environ.get("PTY_PLUGIN_DIRS", "").split(os.pathsep):
        if d and os.path.isfile(os.path.join(d, _MANIFEST_FILE)):
            dirs.append(os.path.abspath(d))
    return dirs


_registry_cfg = _load_json(_REGISTRY_JSON, None)

# registry.json 缺失视为插件系统禁用（总开关语义）
ENABLED = bool(_registry_cfg.get("enabled", True)) if _registry_cfg is not None else False
PLUGIN_DIRS = _discover_plugin_dirs() if ENABLED else []

_reg_states = _registry_cfg.get("plugins", {}) if _registry_cfg is not None else {}
if isinstance(_reg_states, dict):
    PLUGIN_STATES = {
        pid: bool(st.get("enabled", True))
        for pid, st in _reg_states.items()
        if isinstance(st, dict)
    }
else:
    PLUGIN_STATES = {}

_policy_full = _load_json(_POLICY_JSON, {})
_policy_plugins = _policy_full.get("plugins")
POLICY = _policy_plugins if isinstance(_policy_plugins, dict) else {}


class PluginStateStore:
    """registry.json 状态读写（enable/disable 持久化，线程安全）"""

    def __init__(self, path=None):
        self._path = path or _REGISTRY_JSON
        self._lock = threading.Lock()

    def set(self, plugin_id: str, enabled: bool) -> None:
        """记录插件启用状态；registry.json 缺失时以默认结构创建"""
        with self._lock:
            data = _load_json(self._path, {}) or {}
            plugins = data.get("plugins")
            if not isinstance(plugins, dict):
                plugins = {}
            plugins[plugin_id] = {"enabled": bool(enabled)}
            data["plugins"] = plugins
            data.setdefault("enabled", True)
            self._save(data)

    def delete(self, plugin_id: str) -> None:
        """移除插件状态记录（卸载时调用）"""
        with self._lock:
            data = _load_json(self._path, {}) or {}
            plugins = data.get("plugins")
            if isinstance(plugins, dict) and plugin_id in plugins:
                del plugins[plugin_id]
                data["plugins"] = plugins
                self._save(data)

    def _save(self, data: dict) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            _logger.warning("registry.json 写入失败: %s", e)


try:
    from ..logging import get_logger as _get_logger
    _logger = _get_logger("pty-plugins")
except Exception:  # 日志系统未就绪时静默（模块级 import 阶段）
    import logging as _logging
    _logger = _logging.getLogger("pty-plugins")


__all__ = [
    "ENABLED",
    "PLUGIN_DIRS",
    "PLUGIN_STATES",
    "PLUGINS_ROOT",
    "POLICY",
    "PluginStateStore",
]