"""CLI 侧插件宿主 — 客户端进程内的插件钩子链

CLI 插件（清单 kind=cli）在每次客户端进程启动时由 CliPluginHost 加载
（与 daemon 插件同一清单体系：config/plugins/<id>/plugin.json），
处理命令请求/响应的三阶段钩子（经 HookEngine 按优先级调度）：

- before_request：       请求发送前；返回 dict 替换 msg，None 放行（modify）
- transform_response：   响应收到后；返回 dict 替换 resp，None 不变（modify）
- render_response：      响应打印前；返回 str 则打印该文本（provide，首个非 None）

插件声明的 CLI 选项（cliOptions）经 option_registrations() 供解析器注册；
本次调用显式提供的选项值经 activate(names, options) 注入，三阶段钩子内
经 CliContext.options 读取。CLI 选项冲突（与内置参数或其它插件）的插件
跳过加载。

挂载来源：exec 的 --plugin 显式挂载；read/send/mouse 按会话在 daemon 上
记录的 CLI 挂载列表自动挂钩。钩子异常统一隔离：记日志不中断命令流程。
"""

from typing import Dict, List, Optional

from ..plugins.base import Plugin
from ..plugins.config import PluginConfig
from ..plugins.hooks import HookEngine
from ..plugins.loader import load_plugins
from ..plugins.cli_options import (
    build_option_registrations,
    check_cli_option_conflicts,
)
from ..logging import get_logger

_logger = get_logger("pty-client")


class CliContext:
    """CLI 插件运行时上下文 — 每个钩子调用时由宿主构造

    Attributes:
        command:     当前命令名（如 "exec"、"send"）。
        client:      Client 实例引用（插件经其访问配置/画布等）。
        plugin:      插件实例自身。
        output_path: 本次调用的 -o 输出路径（无则 None；fileOutput 类插件读取）。
        config:      插件配置视图（清单默认 + 内存覆盖）。
        options:     本次调用显式提供的插件选项（cliOptions 声明；未提供为空 dict）。
    """

    __slots__ = ("command", "client", "plugin", "output_path", "config", "options")

    def __init__(self, command, client, plugin, output_path=None, config=None,
                 options=None):
        self.command = command
        self.client = client
        self.plugin = plugin
        self.output_path = output_path
        self.config = config
        self.options = options or {}


class CliPluginHost:
    """CLI 插件宿主（空链时所有调用零开销短路）"""

    def __init__(self, plugin_dirs: List[str], client=None):
        self._client = client
        self._last_command = ""
        self._active: set = set()
        self._options: dict = {}
        self._output_path: Optional[str] = None
        self._plugins: List[Plugin] = []
        self._configs: dict = {}
        self._option_manifests: List = []
        self._regs: Dict[str, list] = {}
        self._engine = HookEngine()
        self._plugin_dirs = plugin_dirs
        items = load_plugins(plugin_dirs)
        # 收集插件声明的 CLI 命令类（kind 含 cli 且导出 commands）
        self._command_classes: List[type] = []
        # 全部已加载插件的 kind 形态（含 daemon 侧：reload 按形态分发用）
        self._kinds: Dict[str, List[str]] = {
            item.manifest.id: list(item.manifest.kind) for item in items
        }
        # CLI 选项冲突检测覆盖全部清单（含 daemon 形态：交叉冲突双侧一致）
        manifests = [i.manifest for i in items]
        conflicted = check_cli_option_conflicts(manifests)
        # 注册描述只计算一次（冲突集复用，避免重复检测）
        self._regs = build_option_registrations(manifests, conflicted=conflicted)
        for item in items:
            manifest = item.manifest
            if "cli" not in manifest.kind:
                continue  # 仅加载 CLI 形态插件；daemon 形态仅参与选项/冲突计算
            if manifest.id in conflicted:
                _logger.error(
                    "插件 %s 因 CLI 选项冲突跳过加载: %s",
                    manifest.id, conflicted[manifest.id],
                )
                continue
            if manifest.cli_options:
                self._option_manifests.append(manifest)
            if item.command_classes:
                self._command_classes.extend(item.command_classes)
            try:
                inst = item.cls()
                config = PluginConfig(
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

    def command_classes(self) -> List[type]:
        """插件导出的 CLI Command 类（供 CommandRegistry 注册新命令）"""
        return list(self._command_classes)

    def option_registrations(self) -> Dict[str, list]:
        """插件 CLI 选项的 argparse 注册描述（非冲突插件；供解析器注册）"""
        return self._regs

    def info_for(self, name: str) -> dict:
        """CLI 插件展示信息（version + cliOptions；plugin list 合并条目用）"""
        info = {"version": "", "cliOptions": []}
        for p in self._plugins:
            if p.name == name:
                info["version"] = p.version
        for manifest in self._option_manifests:
            if manifest.id == name:
                info["cliOptions"] = [o.to_dict() for o in manifest.cli_options]
        return info

    def reload(self, name: str) -> Optional[str]:
        """热重载单个 CLI 插件（重新加载代码与清单，保持启用状态）

        供 ``plugin reload <name>`` 对 CLI 形态插件使用（daemon 侧无法重载
        客户端进程内的插件）。重载失败返回错误消息（插件保持原状态）。

        Returns:
            None=成功；str=错误消息。
        """
        from ..plugins.loader import load_plugin_dir

        old = next((p for p in self._plugins if p.name == name), None)
        if old is None:
            return f"CLI 插件未加载: {name}"
        path = getattr(getattr(old, "manifest", None), "path", None)
        if not path:
            return f"无法定位插件目录: {name}"
        item = load_plugin_dir(path)
        if item is None:
            _logger.error("CLI 插件重载失败（加载错误），保持原实例: %s", name)
            return f"插件重载失败: {name}（加载错误，见日志）"
        manifest = item.manifest
        # 卸载旧实例的钩子注册，装载新实例
        self._engine.unregister(old)
        try:
            inst = item.cls()
            config = PluginConfig(
                manifest.config_defaults, manifest.config_schema,
            )
        except Exception:
            _logger.exception("CLI 插件重载实例化失败: %s", manifest.id)
            # 恢复旧实例钩子（重载失败回滚）
            self._engine.register(old, getattr(old, "manifest", None))
            return f"插件重载失败: {name}（实例化错误）"
        self._plugins = [p for p in self._plugins if p.name != name]
        self._plugins.append(inst)
        self._configs.pop(name, None)
        self._configs[inst.name] = config
        self._kinds[name] = list(manifest.kind)
        self._engine.register(inst, manifest)
        try:
            inst.on_init(CliContext("", self._client, inst, None, config))
        except Exception:
            _logger.exception("CLI 插件 %s on_init 异常", inst.name)
        _logger.info(
            "CLI 插件已重载: %s v%s commands=%s",
            inst.name, inst.version, list(manifest.commands),
        )
        return None

    def set_client(self, client) -> None:
        """回填客户端引用（Client 构造完成后；插件经 CliContext.client 访问）"""
        self._client = client

    def is_empty(self) -> bool:
        return not self._plugins

    def names(self) -> List[str]:
        return [p.name for p in self._plugins]

    def kinds_of(self, name: str) -> Optional[List[str]]:
        """查询插件声明的 kind 形态列表（含 daemon 侧形态；未加载返回 None）

        供 plugin reload 按形态分发：双形态插件须 daemon 与本地双侧重载，
        纯 cli 形态只本地重载。
        """
        kinds = self._kinds.get(name)
        return list(kinds) if kinds is not None else None

    def activate(self, names, options=None) -> None:
        """挂载本次调用参与钩子链的 CLI 插件（与 daemon 侧 attach 同语义）

        options: 本次调用显式提供的插件选项 {插件 id: {选项名: 值}}；
        钩子经 CliContext.options 读取本插件切片。
        """
        self._active = set(names)
        self._options = options or {}

    def set_output_path(self, path: Optional[str]) -> None:
        self._output_path = path

    def _ctx(self, plugin):
        return CliContext(
            self._last_command, self._client, plugin,
            self._output_path, self._configs.get(plugin.name),
            self._options.get(plugin.name, {}),
        )

    def _pred(self, plugin) -> bool:
        if plugin.name not in self._active:
            # autoMount 声明的命令：无需 --plugin 显式激活，自动参与钩子链
            auto = getattr(plugin.manifest, "auto_mount", []) if plugin.manifest is not None else []
            if self._last_command not in auto:
                return False
        commands = getattr(plugin.manifest, "commands", []) if plugin.manifest is not None else []
        return not commands or self._last_command in commands

    def check_request(self, command: str, msg: dict) -> Optional[str]:
        """请求发送前拦截检查：插件返回 None 放行，返回 str 拒绝（理由透传用户）

        在 before_request 之前调用。首个拒绝的插件生效，后续不再检查。
        """
        self._last_command = command
        reason = self._engine.dispatch_provide(
            "check_request", self._ctx, msg, predicate=self._pred
        )
        if isinstance(reason, str):
            _logger.info("插件拒绝请求 %s: %s", command, reason)
        return reason if isinstance(reason, str) else None

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