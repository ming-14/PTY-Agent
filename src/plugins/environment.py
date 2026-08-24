"""插件运行环境 — daemon 全局共享的插件能力集合

每个插件一份：配置视图（PluginConfig）、存储入口（PluginStorage）、权限检查器
（PermissionChecker）、清单引用；另持 daemon 级事件总线。环境由注册表在加载时
构建，会话宿主与调度器经环境向插件暴露能力（插件不直接接触内核）。
"""

import os
from typing import Dict, Optional

from ..config.common import DATA_DIR
from ..logging import get_logger
from .config import PluginConfig
from .events import EventBus
from .permissions import PermissionChecker
from .storage import PluginStorage

_logger = get_logger("pty-plugins")

# 插件数据根目录（各插件在 <root>/<id>/ 下）
DATA_ROOT = os.path.join(DATA_DIR, "plugins")


class PluginEnvironment:
    """插件运行环境（daemon 全局单例，注册表持有）"""

    def __init__(self, policy: Optional[dict] = None):
        self.events = EventBus()
        self._policy = policy if isinstance(policy, dict) else {}
        self._manifests: dict = {}
        self._configs: Dict[str, PluginConfig] = {}
        self._storages: Dict[str, PluginStorage] = {}
        self._permissions: Dict[str, PermissionChecker] = {}

    def register(self, manifest) -> None:
        """注册插件能力

        配置加载/校验失败抛 ConfigError（由注册表转为 BROKEN 状态）。
        """
        plugin_id = manifest.id
        self._manifests[plugin_id] = manifest
        self._configs[plugin_id] = PluginConfig(
            plugin_id, manifest.path, manifest.config_defaults, manifest.config_schema
        )
        self._storages[plugin_id] = PluginStorage(
            os.path.join(DATA_ROOT, plugin_id)
        )
        entry = self._policy.get(plugin_id)
        entry = entry if isinstance(entry, dict) else {}
        self._permissions[plugin_id] = PermissionChecker(
            plugin_id,
            manifest.permissions,
            list(entry.get("grant", [])),
            list(entry.get("deny", [])),
        )

    def unregister(self, plugin_id: str) -> None:
        """移除插件能力（reload/卸载时调用）"""
        self._manifests.pop(plugin_id, None)
        self._configs.pop(plugin_id, None)
        self._storages.pop(plugin_id, None)
        self._permissions.pop(plugin_id, None)

    def manifest(self, plugin_id: str):
        return self._manifests.get(plugin_id)

    def config_for(self, plugin_id: str) -> Optional[PluginConfig]:
        return self._configs.get(plugin_id)

    def storage_for(self, plugin_id: str) -> Optional[PluginStorage]:
        return self._storages.get(plugin_id)

    def permission_for(self, plugin_id: str) -> Optional[PermissionChecker]:
        return self._permissions.get(plugin_id)

    def logger_for(self, plugin_id: str):
        """插件共享日志器（日志分组 pty-plugins；插件名作消息上下文）"""
        return _logger