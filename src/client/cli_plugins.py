"""CLI 侧插件宿主 — 客户端进程内的插件钩子链

CLI 插件（Plugin.kind == "cli"）在每次客户端进程启动时由 CliPluginHost
加载（与 daemon 插件同一注册体系：config/plugins/plugins.json），
处理命令请求/响应的三阶段钩子：

- before_request：       请求发送前；返回 dict 替换 msg，None 放行
- transform_response：   响应收到后、业务后处理前；返回 dict 替换 resp，None 不变
- render_response：      响应打印前；返回 str 则打印该文本，None 走默认 JSON

与 daemon 侧 PluginHost 的定位一致：插件一经挂载到会话，宿主按钩子自动派发回调，
无启用/禁用概念。挂载来源：
- exec 的 --plugin 显式挂载
- read/send/mouse 按会话在 daemon 上记录的 CLI 挂载列表自动挂钩
区别仅在与 daemon 侧支持的钩子种类不同。钩子异常统一隔离：记日志不中断命令流程。
"""

from typing import List, Optional

from ..plugins.base import Plugin  # noqa: F401   （导出约定：插件模块 import 使用）
from ..plugins.loader import load_plugins, resolve_kind
from ..logging import get_logger

_logger = get_logger("pty-client")


class CliContext:
    """CLI 插件运行时上下文 — 每个钩子调用时由宿主构造

    Attributes:
        command:     当前命令名（如 "exec"、"send"）。
        client:      Client 实例引用（插件经其访问配置/画布等）。
        plugin:      插件实例自身。
        output_path: 本次调用的 -o 输出路径（无则 None；fileOutput 类插件读取）。
    """

    __slots__ = ("command", "client", "plugin", "output_path")

    def __init__(self, command: str, client, plugin, output_path=None):
        self.command = command
        self.client = client
        self.plugin = plugin
        self.output_path = output_path


class CliPluginHost:
    """CLI 插件宿主（空链时所有调用零开销短路）"""

    def __init__(self, plugin_paths: List[str], client=None):
        self._client = client
        # 最近一次请求的命令名（render 阶段经 formatter 钩子调用时无 command 入参，
        # CLI 单命令进程内顺序执行，用最近命令过滤即可）
        self._last_command = ""
        # 本次调用挂载的 CLI 插件名（exec --plugin 或 read/send/mouse 按会话挂载列表）
        self._active: set = set()
        # 本次调用的 -o 输出路径（cmd_* 在 _send_recv 前设置，供 fileOutput 插件读取）
        self._output_path: Optional[str] = None
        self._plugins: List[Plugin] = []
        for cls in load_plugins(plugin_paths):
            if resolve_kind(cls) != "cli":
                continue
            try:
                inst = cls()
            except Exception:
                _logger.exception("CLI 插件实例化失败，跳过: %s", cls.name)
                continue
            self._plugins.append(inst)
            _logger.info(
                "CLI 插件已加载: %s v%s commands=%s",
                inst.name,
                inst.version,
                list(inst.commands),
            )

    def is_empty(self) -> bool:
        return not self._plugins

    def names(self) -> List[str]:
        return [p.name for p in self._plugins]

    def activate(self, names) -> None:
        """挂载本次调用参与钩子链的 CLI 插件

        来源为 exec 的 --plugin 或会话在 daemon 上记录的 CLI 挂载列表。
        与 daemon 侧 attach 同语义：挂上即由宿主自动派发回调，无启用/禁用。
        """
        self._active = set(names)

    def set_output_path(self, path: Optional[str]) -> None:
        """记录本次调用的 -o 输出路径（供 fileOutput 类插件读取）"""
        self._output_path = path

    def last_command(self) -> str:
        return self._last_command

    def _for_command(self, command: str) -> List[Plugin]:
        """按挂载状态与命令名过滤插件

        - 仅已挂载的插件参与钩子链
        - commands 空=全部命令，非空=仅在列出的命令生效
        """
        if not command:
            return [p for p in self._plugins if p.name in self._active]
        return [
            p
            for p in self._plugins
            if p.name in self._active
            and (not p.commands or command in p.commands)
        ]

    def before_request(self, command: str, msg: dict) -> dict:
        """请求发送前链式变换；插件返回 None 表示不修改"""
        self._last_command = command
        for plugin in self._for_command(command):
            try:
                result = plugin.before_request(
                    CliContext(command, self._client, plugin, self._output_path), msg
                )
            except Exception:
                _logger.exception("CLI 插件 %s before_request 异常", plugin.name)
                continue
            if result is not None:
                msg = result
        return msg

    def transform_response(self, command: str, resp: dict) -> dict:
        """响应收到后链式变换；插件返回 None 表示不修改"""
        for plugin in self._for_command(command):
            try:
                result = plugin.transform_response(
                    CliContext(command, self._client, plugin, self._output_path), resp
                )
            except Exception:
                _logger.exception("CLI 插件 %s transform_response 异常", plugin.name)
                continue
            if result is not None:
                resp = result
        return resp

    def render_response(self, command: str, resp: dict) -> Optional[str]:
        """响应打印前渲染；首个返回非 None 文本的插件生效"""
        for plugin in self._for_command(command):
            try:
                text = plugin.render_response(
                    CliContext(command, self._client, plugin, self._output_path), resp
                )
            except Exception:
                _logger.exception("CLI 插件 %s render_response 异常", plugin.name)
                continue
            if text is not None:
                return text
        return None

    def render_hook(self, resp: dict) -> Optional[str]:
        """formatter 渲染钩子入口：按最近命令名调用 render_response"""
        return self.render_response(self._last_command, resp)