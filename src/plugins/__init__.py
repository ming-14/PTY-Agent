"""插件系统 v2 — 清单驱动、标准化生命周期、钩子链引擎、事件总线

包含：
- base:      插件协议（Plugin 基类、PluginContext、HANDLED 哨兵）
- manifest:  plugin.json 清单解析与校验
- loader:    清单驱动加载器（模块导入 + 声明校验）
- registry:  进程级注册表（加载 + 生命周期 enable/disable/reload + auto_load）
- host:      会话级插件宿主（HookEngine 驱动 + 挂载链 + 返回控制）
- hooks:     钩子链引擎（优先级排序 + 五类调度语义）
- events:    daemon 事件总线（pub/sub + 主题通配）
- config:    插件配置（清单默认 + config.yaml + 环境变量覆盖 + schema 校验）
- storage:   插件存储（kv/文件/sqlite 三种视图）
- permissions: 能力检查 + 审计
- environment: 运行环境（daemon 全局共享能力集合）
- io:        进程级插件 I/O 端口（连接收发通道，用于多帧传输协议）
"""

from .base import HANDLED, Plugin, PluginContext, ProcessPluginContext, VALID_HOOKS
from .manifest import PluginManifest, load_manifest
from .loader import LoadedPlugin, load_plugin_dir, load_plugins
from .registry import PluginRegistry
from .host import PluginHost
from .hooks import HookEngine
from .events import EventBus, Event, match_topic
from .config import PluginConfig, ConfigError
from .storage import PluginStorage, KvStore, FileStore
from .permissions import PermissionChecker, PermissionDenied
from .environment import PluginEnvironment

__all__ = [
    "HANDLED",
    "Plugin",
    "PluginContext",
    "ProcessPluginContext",
    "VALID_HOOKS",
    "PluginManifest",
    "load_manifest",
    "LoadedPlugin",
    "load_plugin_dir",
    "load_plugins",
    "PluginRegistry",
    "PluginHost",
    "HookEngine",
    "EventBus",
    "Event",
    "match_topic",
    "PluginConfig",
    "ConfigError",
    "PluginStorage",
    "KvStore",
    "FileStore",
    "PermissionChecker",
    "PermissionDenied",
    "PluginEnvironment",
]