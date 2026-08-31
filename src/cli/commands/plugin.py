"""plugin 命令：插件管理（list/ls/attach/detach/cmd + 生命周期/安装/配置）"""

import argparse

from ..base import Command, CommandContext
from ..common_args import add_common_args
from ...client.presenter import emit, emit_error


class PluginCommand(Command):
    """plugin 命令"""

    name = "plugin"
    help = "插件管理（list/ls/attach/detach/cmd/install/uninstall/enable/disable/reload/info/status/config/gethelp）"
    # 公共参数已在各子子命令解析器手动注册（add_common_args），避免两级重复
    use_common_args = False

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--gethelp", metavar="NAME", default=None,
                            help="显示插件帮助文档（<插件名>.md，按需查看，不自动输出）")
        plugin_sub = parser.add_subparsers(dest="plugin_subcmd", help="插件子命令")

        p_list = plugin_sub.add_parser("list", help="列出已加载插件")
        add_common_args(p_list)

        p_ls = plugin_sub.add_parser("ls", help="列出会话挂载的插件")
        add_common_args(p_ls)
        p_ls.add_argument("id", help="会话标识")

        p_attach = plugin_sub.add_parser("attach", help="动态挂载插件到运行中的会话")
        add_common_args(p_attach)
        p_attach.add_argument("id", help="会话标识")
        p_attach.add_argument("name", help="插件名")

        p_detach = plugin_sub.add_parser("detach", help="从会话卸载插件")
        add_common_args(p_detach)
        p_detach.add_argument("id", help="会话标识")
        p_detach.add_argument("name", help="插件名")

        p_cmd = plugin_sub.add_parser("cmd", help="调用插件自定义命令")
        add_common_args(p_cmd)
        p_cmd.add_argument("id", help="会话标识")
        p_cmd.add_argument("name", help="插件名")
        p_cmd.add_argument("command", help="命令名")
        p_cmd.add_argument("args", nargs="*", default=None, help="命令参数（可选）")

        p_install = plugin_sub.add_parser("install", help="从目录安装插件（含 plugin.json）")
        add_common_args(p_install)
        p_install.add_argument("path", help="插件目录路径")

        p_uninstall = plugin_sub.add_parser("uninstall", help="卸载插件（需先 disable）")
        add_common_args(p_uninstall)
        p_uninstall.add_argument("name", help="插件名")

        p_enable = plugin_sub.add_parser("enable", help="启用插件")
        add_common_args(p_enable)
        p_enable.add_argument("name", help="插件名")

        p_disable = plugin_sub.add_parser("disable", help="停用插件")
        add_common_args(p_disable)
        p_disable.add_argument("name", help="插件名")

        p_reload = plugin_sub.add_parser("reload", help="热重载插件（重新加载代码与清单）")
        add_common_args(p_reload)
        p_reload.add_argument("name", help="插件名")

        p_info = plugin_sub.add_parser("info", help="插件详情（清单/状态/权限/事件）")
        add_common_args(p_info)
        p_info.add_argument("name", help="插件名")

        p_status = plugin_sub.add_parser("status", help="插件运行状态")
        add_common_args(p_status)
        p_status.add_argument("name", help="插件名")

        p_config = plugin_sub.add_parser("config", help="查看/修改插件配置")
        add_common_args(p_config)
        p_config.add_argument("name", help="插件名")
        p_config.add_argument(
            "kv", nargs="*", default=None,
            help="set 形式: key value（value 支持 JSON 类型）；缺省为查看",
        )

    def run(self, args, ctx: CommandContext) -> None:
        # --gethelp 优先于子命令
        if getattr(args, "gethelp", None):
            self._show_help(args.gethelp, ctx)
            return
        sub = args.plugin_subcmd
        if sub == "list":
            ctx.client.cmd_plugin("list")
        elif sub == "ls":
            ctx.client.cmd_plugin("ls", session_id=args.id)
        elif sub == "attach":
            ctx.client.cmd_plugin("attach", session_id=args.id, name=args.name)
        elif sub == "detach":
            ctx.client.cmd_plugin("detach", session_id=args.id, name=args.name)
        elif sub == "cmd":
            ctx.client.cmd_plugin(
                "cmd",
                session_id=args.id,
                name=args.name,
                command=args.command,
                args=args.args,
            )
        elif sub == "install":
            ctx.client.cmd_plugin("install", path=args.path)
        elif sub == "uninstall":
            ctx.client.cmd_plugin("uninstall", name=args.name)
        elif sub == "enable":
            ctx.client.cmd_plugin("enable", name=args.name)
        elif sub == "disable":
            ctx.client.cmd_plugin("disable", name=args.name)
        elif sub == "reload":
            # 按 kind 形态分发：纯 cli 形态在客户端进程内加载，本地重载；
            # 含 process/session 形态的插件须经 daemon 重载（双形态两侧都重载）
            kinds = (
                ctx.cli_plugins.kinds_of(args.name)
                if ctx.cli_plugins is not None
                else None
            )
            if kinds is None:
                # 客户端未加载（纯 daemon 形态或插件系统未启用）→ daemon
                ctx.client.cmd_plugin("reload", name=args.name)
            elif "process" in kinds or "session" in kinds:
                ctx.client.cmd_plugin("reload", name=args.name)
                if "cli" in kinds and args.name in ctx.cli_plugins.names():
                    err = ctx.cli_plugins.reload(args.name)
                    if err:
                        emit_error(err)
                    else:
                        emit(f"已重载 CLI 插件: {args.name}")
            else:
                err = ctx.cli_plugins.reload(args.name)
                if err:
                    emit_error(err)
                else:
                    emit(f"已重载 CLI 插件: {args.name}")
        elif sub == "info":
            ctx.client.cmd_plugin("info", name=args.name)
        elif sub == "status":
            ctx.client.cmd_plugin("status", name=args.name)
        elif sub == "config":
            if args.kv:
                ctx.client.cmd_plugin(
                    "config", name=args.name, key=args.kv[0],
                    value=" ".join(args.kv[1:]),
                )
            else:
                ctx.client.cmd_plugin("config", name=args.name)
        else:
            ctx.parser.print_help()

    def _show_help(self, name: str, ctx: CommandContext) -> None:
        """读取并显示插件帮助文档（<插件名>.md），并标记为已注入（内存态）"""
        import os
        import time
        from ...config.plugins import PLUGIN_DIRS
        from ...plugins.context import (
            find_plugin_dir, context_text, _content_hash,
            load_context_state, save_context_state,
        )
        from ...client.presenter import emit, emit_error

        plugin_dir = find_plugin_dir(PLUGIN_DIRS, name)
        if plugin_dir is None:
            emit_error(f"插件 '{name}' 未找到")
            return
        help_file = os.path.join(plugin_dir, name + ".md")
        if not os.path.isfile(help_file):
            emit_error(f"插件 '{name}' 无帮助文档（{name}.md）")
            return
        try:
            with open(help_file, "r", encoding="utf-8") as f:
                content = f.read()
            emit(content, msg_type="raw")
            # 标记为已注入（内存态，与自动注入状态同文件）
            text = context_text(name, plugin_dir)
            if text:
                digest = _content_hash(text)
                state = load_context_state()
                state[name] = {"sent": True, "sentAt": time.time(), "contentHash": digest}
                save_context_state(state)
        except OSError as e:
            emit_error(f"读取帮助文档失败: {e}")