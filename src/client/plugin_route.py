"""客户端插件路由混入 —— CLI 插件挂载分流、上下文输出与会话挂载自动挂钩。

职责（ClientPluginMixin）：
- _route_plugins：exec --plugin 按形态分流（CLI 形态客户端挂钩 / daemon 形态透传），
  并把启用插件的 <插件名>.md 上下文输出到 CLI（stderr 信息区，给用户看）
- _session_cli_plugins：查询会话挂载的 CLI 插件名
- _activate_session_cli：read/send/mouse 每次调用自动挂钩会话记录的 CLI 插件
"""

import sys

from ..plugins.context import find_plugin_dir, output_context


class ClientPluginMixin:
    """CLI 插件挂载分流与会话挂载自动挂钩"""

    def _route_plugins(self, msg: dict, plugins) -> None:
        """exec --plugin 按插件形态分流挂载：CLI 形态客户端挂钩，daemon 形态透传

        插件在类声明处用 kind 声明自己支持哪侧钩子：
        - kind=cli：客户端进程内执行，本次调用挂载钩子（CliPluginHost.activate），
          并经 msg["cliPlugins"] 记录到会话，后续 read/send/mouse 客户端自动挂钩
        - kind=session/process：daemon 侧挂载，经 msg["plugins"] 透传会话创建

        分流同时把启用插件的上下文（<插件名>.md）输出到 CLI 给用户看。
        """
        if not plugins:
            return
        cli_plugins = self._cli_plugins
        cli_names = cli_plugins.names() if cli_plugins is not None else []
        cli_hooks = []
        daemon_names = []
        for name in plugins:
            if cli_plugins is not None and name in cli_names:
                cli_hooks.append(name)
            else:
                daemon_names.append(name)
        if cli_hooks:
            cli_plugins.activate(cli_hooks)
            msg["cliPlugins"] = cli_hooks
        if daemon_names:
            msg["plugins"] = daemon_names
        self._print_plugin_contexts(plugins)

    def _print_plugin_contexts(self, plugins) -> None:
        """把指定插件（已启用）的上下文（<插件名>.md）输出到 CLI stderr

        exec --plugin 启用插件时调用；无上下文文件/读取失败静默跳过，
        显式禁用的插件（registry.json）不输出。
        """
        try:
            from ..config.plugins import PLUGIN_DIRS
            from ..plugins.context import disabled_plugin_names
        except Exception:
            return
        disabled = disabled_plugin_names()
        for name in plugins:
            if name in disabled:
                continue
            try:
                plugin_dir = find_plugin_dir(PLUGIN_DIRS, name)
                if plugin_dir is not None:
                    output_context(sys.stderr, name, plugin_dir)
            except Exception:
                continue

    def _session_cli_plugins(self, session_id: str) -> list:
        """查询会话挂载的 CLI 插件名（exec 时经 cliPlugins 记录在 daemon 会话上）

        read/send/mouse 每次调用据此自动挂钩，无需再传 --plugin。
        会话不存在/已结束返回空列表（CLI 插件仅在会话存活时回调）。
        """
        if self._cli_plugins is None:
            return []
        resp = self._send_recv(
            {"type": "plugin", "action": "ls", "id": session_id},
            autostart=False,
        )
        if resp.get("type") == "error":
            return []
        mounted = resp.get("plugins") or []
        names = [p.get("name") for p in mounted if isinstance(p, dict)]
        return [n for n in names if n in self._cli_plugins.names()]

    def _activate_session_cli(self, session_id: str) -> None:
        """挂载会话上记录的 CLI 插件钩子（read/send/mouse 每次调用自动生效）"""
        if self._cli_plugins is None:
            return
        self._cli_plugins.activate(self._session_cli_plugins(session_id))