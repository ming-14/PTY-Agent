"""CLI 侧插件宿主 — 客户端进程内的插件钩子链

CLI 插件（清单 kind=cli）在每次客户端进程启动时由 CliPluginHost 加载
（与 daemon 插件同一清单体系：config/plugins/<id>/plugin.json），
处理命令请求/响应的三阶段钩子（经 HookEngine 按优先级调度）：

- before_request：       请求发送前；返回 dict 替换 msg，None 放行（modify）
- transform_response：   响应收到后；返回 dict 替换 resp，None 不变（modify）
- render_response：      响应打印前；返回 str 则打印该文本（provide，首个非 None）

挂载来源：exec 的 --plugin 显式挂载；read/send/mouse 按会话在 daemon 上
记录的 CLI 挂载列表自动挂钩。钩子异常统一隔离：记日志不中断命令流程。
"""

from typing import List, Optional

from ..plugins.base import Plugin
from ..plugins.config import PluginConfig, ConfigError
from ..plugins.hooks import HookEngine
from ..plugins.loader import load_plugins
from ..logging import get_logger

_logger = get_logger("pty-client")


class CliContext:
    """CLI 插件运行时上下文 — 每个钩子调用时由宿主构造

    Attributes:
        command:     当前命令名（如 "exec"、"send"）。
        client:      Client 实例引用（插件经其访问配置/画布等）。
        plugin:      插件实例自身。
        output_path: 本次调用的 -o 输出路径（无则 None；fileOutput 类插件读取）。
        config:      插件配置视图（清单默认 + config.yaml + 环境变量）。
    """

    __slots__ = ("command", "client", "plugin", "output_path", "config")

    def __init__(self, command, client, plugin, output_path=None, config=None):
        self.command = command
        self.client = client
        self.plugin = plugin
        self.output_path = output_path
        self.config = config


class CliPluginHost:
    """CLI 插件宿主（空链时所有调用零开销短路）"""

    def __init__(self, plugin_dirs: List[str], client=None):
        self._client = client
        self._last_command = ""
        self._active: set = set()
        self._output_path: Optional[str] = None
        self._plugins: List[Plugin] = []
        self._configs: dict = {}
        self._engine = HookEngine()
        for item in load_plugins(plugin_dirs):
            manifest = item.manifest
            if manifest.kind != "cli":
                continue
            try:
                inst = item.cls()
                config = PluginConfig(
                    manifest.id, manifest.path,
                    manifest.config_defaults, manifest.config_schema,
                )
            except Exception:
                _logger.exception("CLI 插件加载失败，跳过: %s", manifest.id)
                continue
            self._plugins.append(inst)
            self._configs[inst.name] = config
            self._engine.register(inst, manifest)
            try:
                inst.on_init(CliContext("", self._client, inst, None, config))
            except Exception:
                _logger.exception("CLI 插件 %s on_init 异常", inst.name)
            _logger.info(
                "CLI 插件已加载: %s v%s commands=%s",
                inst.name, inst.version, list(manifest.commands),
            )

    def is_empty(self) -> bool:
        return not self._plugins

    def names(self) -> List[str]:
        return [p.name for p in self._plugins]

    def activate(self, names) -> None:
        """挂载本次调用参与钩子链的 CLI 插件（与 daemon 侧 attach 同语义）"""
        self._active = set(names)

    def set_output_path(self, path: Optional[str]) -> None:
        self._output_path = path

    def last_command(self) -> str:
        return self._last_command

    def _ctx(self, plugin):
        return CliContext(
            self._last_command, self._client, plugin,
            self._output_path, self._configs.get(plugin.name),
        )

    def _pred(self, plugin) -> bool:
        if plugin.name not in self._active:
            return False
        commands = getattr(plugin.manifest, "commands", []) if plugin.manifest is not None else []
        return not commands or self._last_command in commands

    def before_request(self, command: str, msg: dict) -> dict:
        """请求发送前链式变换；插件返回 None 表示不修改"""
        self._last_command = command
        return self._engine.dispatch_modify(
            "before_request", self._ctx, msg, predicate=self._pred, intercept=False
        )

    def transform_response(self, command: str, resp: dict) -> dict:
        """响应收到后链式变换；插件返回 None 表示不修改"""
        self._last_command = command
        return self._engine.dispatch_modify(
            "transform_response", self._ctx, resp, predicate=self._pred, intercept=False
        )

    def render_response(self, command: str, resp: dict) -> Optional[str]:
        """响应打印前渲染；首个返回非 None 文本的插件生效"""
        self._last_command = command
        return self._engine.dispatch_provide(
            "render_response", self._ctx, resp, predicate=self._pred
        )

    def render_hook(self, resp: dict) -> Optional[str]:
        """formatter 渲染钩子入口：按最近命令名调用 render_response"""
        return self.render_response(self._last_command, resp)