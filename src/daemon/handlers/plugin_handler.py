"""plugin 命令处理 — 插件管理（生命周期 + 挂载 + 命令）

消息格式:
    {"type": "plugin", "action": "list"}
    {"type": "plugin", "action": "ls",        "id": "<sid>"}
    {"type": "plugin", "action": "attach",    "id": "<sid>", "name": "<plugin>"}
    {"type": "plugin", "action": "detach",    "id": "<sid>", "name": "<plugin>"}
    {"type": "plugin", "action": "cmd",       "id": "<sid>", "name": "<plugin>",
     "command": ..., "args": [...]}
    {"type": "plugin", "action": "install",   "path": "<dir>"}
    {"type": "plugin", "action": "uninstall", "name": "<plugin>"}
    {"type": "plugin", "action": "enable",    "name": "<plugin>"}
    {"type": "plugin", "action": "disable",   "name": "<plugin>"}
    {"type": "plugin", "action": "reload",    "name": "<plugin>"}
    {"type": "plugin", "action": "info",      "name": "<plugin>"}
    {"type": "plugin", "action": "status",    "name": "<plugin>"}
    {"type": "plugin", "action": "config",    "name": "<plugin>", "sub": "get"|"set",
     "key": "...", "value": "..."}

attach/detach/cmd 作用于运行中的会话；enable/disable/reload/uninstall/config
作用于全局插件生命周期；install 从目录安装新插件。
"""

import json
import os

from ...config.common import MAX_COMMAND_LEN, MAX_SESSION_ID_LEN
from ...config.plugins import PLUGINS_ROOT
from ...plugins.registry import PluginRegistry
from ...plugins.config import ConfigError
from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler
from ...execution.context import HandlerContext
from ...execution.utils import validate_field
from ...logging import get_logger

_logger = get_logger("pty-daemon")

_MAX_PLUGIN_NAME_LEN = 64
_MAX_PATH_LEN = 4096
_MAX_CONFIG_VALUE_LEN = 4096


class PluginHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        action = msg.get("action", "")
        session_id = msg.get("id", "")
        name = msg.get("name", "")
        path = msg.get("path", "")

        if action in ("attach", "detach", "cmd", "info", "status", "config"):
            if not validate_field(name, "name", _MAX_PLUGIN_NAME_LEN, conn):
                return
        if action in ("enable", "disable", "reload", "uninstall", "info", "status"):
            if not validate_field(name, "name", _MAX_PLUGIN_NAME_LEN, conn):
                return
        if action in ("ls", "attach", "detach", "cmd"):
            if not validate_field(session_id, "id", MAX_SESSION_ID_LEN, conn):
                return
        if action == "install" and not validate_field(path, "path", _MAX_PATH_LEN, conn):
            return

        registry = ctx.manager.plugin_registry if ctx.manager else None

        if action == "list":
            self._handle_list(registry, conn)
        elif action == "ls":
            self._handle_ls(ctx, conn, session_id)
        elif action == "attach":
            self._handle_attach(ctx, registry, conn, session_id, name)
        elif action == "detach":
            self._handle_detach(ctx, conn, session_id, name)
        elif action == "cmd":
            self._handle_cmd(ctx, registry, conn, msg, session_id, name)
        elif action == "install":
            self._handle_install(registry, conn, path)
        elif action == "uninstall":
            self._handle_uninstall(registry, conn, name)
        elif action == "enable":
            self._handle_enable(registry, conn, name)
        elif action == "disable":
            self._handle_disable(registry, conn, name)
        elif action == "reload":
            self._handle_reload(registry, conn, name)
        elif action == "info":
            self._handle_info(registry, conn, name)
        elif action == "status":
            self._handle_info(registry, conn, name)  # status 与 info 同形状，客户端子集渲染
        elif action == "config":
            self._handle_config(registry, conn, name, msg)
        else:
            Message.send(conn, Response.error(f"未知 plugin action: {action}"))

    # ── 挂载管理 ──────────────────────────────────────────

    def _handle_list(self, registry, conn):
        if registry is None:
            Message.send(conn, Response.error("插件系统未启用"))
            return
        plugins = registry.list_all()
        Message.send(
            conn,
            Response.command_result("plugin", None, action="list", plugins=plugins),
        )

    def _handle_ls(self, ctx, conn, session_id):
        session = ctx.manager.get_session(session_id) if ctx.manager else None
        if session is None:
            Message.send(conn, Response.error(f"会话 '{session_id}' 不存在"))
            return
        plugins = session.plugin_host.snapshot_info() or []
        for name in getattr(session, "cli_plugin_names", None) or []:
            if not any(p.get("name") == name for p in plugins):
                plugins.append({"name": name, "version": "", "cli": True})
        Message.send(
            conn,
            Response.command_result("plugin", session_id, action="ls", plugins=plugins),
        )

    def _handle_attach(self, ctx, registry, conn, session_id, name):
        if registry is None:
            Message.send(conn, Response.error("插件系统未启用"))
            return
        session = ctx.manager.get_session(session_id) if ctx.manager else None
        if session is None:
            Message.send(conn, Response.error(f"会话 '{session_id}' 不存在"))
            return
        if not session.running:
            Message.send(conn, Response.error(f"会话 '{session_id}' 已结束"))
            return
        inst = registry.instantiate(name)
        if inst is None:
            entry = registry._entries.get(name) if hasattr(registry, "_entries") else None
            if entry is not None and entry.manifest.kind == "process":
                msg_types = ", ".join(list(entry.manifest.message_types)[:3])
                Message.send(
                    conn,
                    Response.error(
                        f"插件 {name} 为进程级插件（经 {msg_types} 等消息直调），不支持会话挂载"
                    ),
                )
            else:
                Message.send(conn, Response.error(f"插件未加载: {name}"))
            return
        if not session.plugin_host.attach(inst):
            Message.send(conn, Response.error(f"插件 {name} 已挂载到会话，禁止重复挂载"))
            return
        _logger.info("plugin attach: sid=%r name=%r", session_id, name)
        Message.send(
            conn,
            Response.command_result(
                "plugin",
                session_id,
                action="attach",
                plugin=name,
                plugins=session.plugin_host.snapshot_info() or [],
            ),
        )

    def _handle_detach(self, ctx, conn, session_id, name):
        session = ctx.manager.get_session(session_id) if ctx.manager else None
        if session is None:
            Message.send(conn, Response.error(f"会话 '{session_id}' 不存在"))
            return
        if not session.plugin_host.detach(name):
            Message.send(conn, Response.error(f"插件未挂载: {name}"))
            return
        _logger.info("plugin detach: sid=%r name=%r", session_id, name)
        Message.send(
            conn,
            Response.command_result(
                "plugin",
                session_id,
                action="detach",
                plugin=name,
                plugins=session.plugin_host.snapshot_info() or [],
            ),
        )

    def _handle_cmd(self, ctx, registry, conn, msg, session_id, name):
        command = msg.get("command", "")
        if not validate_field(command, "command", MAX_COMMAND_LEN, conn):
            return
        session = ctx.manager.get_session(session_id) if ctx.manager else None
        if session is None:
            Message.send(conn, Response.error(f"会话 '{session_id}' 不存在"))
            return
        result = session.plugin_host.handle_command(name, msg)
        if result is None:
            if registry is not None:
                entry = registry._entries.get(name) if hasattr(registry, "_entries") else None
                if entry is not None and entry.manifest.kind == "process":
                    msg_types = ", ".join(list(entry.manifest.message_types)[:3])
                    Message.send(
                        conn,
                        Response.error(
                            f"插件 {name} 为进程级插件（经 {msg_types} 等消息直调），不支持 plugin cmd"
                        ),
                    )
                    return
            Message.send(conn, Response.error(f"插件 {name} 未处理命令: {command}"))
            return
        Message.send(
            conn,
            Response.command_result(
                "plugin",
                session_id,
                action="cmd",
                plugin=name,
                command=command,
                result=result,
            ),
        )

    # ── 生命周期管理 ──────────────────────────────────────

    def _handle_install(self, registry, conn, path):
        if registry is None:
            Message.send(conn, Response.error("插件系统未启用"))
            return
        if not os.path.isdir(path):
            Message.send(conn, Response.error(f"安装路径不是目录: {path}"))
            return
        plugin_id = registry.load_dir(path, PLUGINS_ROOT)
        if plugin_id is None:
            Message.send(conn, Response.error("安装失败，请检查插件目录与清单"))
            return
        Message.send(conn, Response.command_result(
            "plugin", None, action="install",
            message="已安装: %s (未启用，执行 plugin enable %s 激活)" % (plugin_id, plugin_id),
        ))

    def _handle_uninstall(self, registry, conn, name):
        if registry is None:
            Message.send(conn, Response.error("插件系统未启用"))
            return
        if not registry.has(name):
            Message.send(conn, Response.error(f"插件未加载: {name}"))
            return
        if not registry.remove(name):
            Message.send(conn, Response.error("卸载失败（插件可能仍处于启用状态，请先 disable）"))
            return
        Message.send(conn, Response.command_result(
            "plugin", None, action="uninstall",
            message="已卸载: %s" % name,
        ))

    def _handle_enable(self, registry, conn, name):
        if registry is None:
            Message.send(conn, Response.error("插件系统未启用"))
            return
        if not registry.has(name):
            Message.send(conn, Response.error(f"插件未加载: {name}"))
            return
        if not registry.enable(name):
            Message.send(conn, Response.error("启用失败（插件可能已损坏，请执行 plugin info %s 查看详情）" % name))
            return
        Message.send(conn, Response.command_result(
            "plugin", None, action="enable",
            message="已启用: %s" % name,
        ))

    def _handle_disable(self, registry, conn, name):
        if registry is None:
            Message.send(conn, Response.error("插件系统未启用"))
            return
        if not registry.has(name):
            Message.send(conn, Response.error(f"插件未加载: {name}"))
            return
        if not registry.disable(name):
            Message.send(conn, Response.error("停用失败（插件可能未启用）"))
            return
        Message.send(conn, Response.command_result(
            "plugin", None, action="disable",
            message="已禁用: %s" % name,
        ))

    def _handle_reload(self, registry, conn, name):
        if registry is None:
            Message.send(conn, Response.error("插件系统未启用"))
            return
        if not registry.has(name):
            Message.send(conn, Response.error(f"插件未加载: {name}"))
            return
        if not registry.reload(name):
            Message.send(conn, Response.error("重载失败"))
            return
        Message.send(conn, Response.command_result(
            "plugin", None, action="reload",
            message="已重载: %s" % name,
        ))

    def _handle_info(self, registry, conn, name):
        if registry is None:
            Message.send(conn, Response.error("插件系统未启用"))
            return
        info = registry.info(name) if registry.has(name) else None
        if info is None:
            Message.send(conn, Response.error(f"插件未加载: {name}"))
            return
        Message.send(conn, Response.command_result(
            "plugin", None, action="info", info=info,
        ))

    def _handle_config(self, registry, conn, name, msg):
        if registry is None:
            Message.send(conn, Response.error("插件系统未启用"))
            return
        if not registry.has(name):
            Message.send(conn, Response.error(f"插件未加载: {name}"))
            return
        config = registry.environment.config_for(name)
        if config is None:
            Message.send(conn, Response.error(f"插件 {name} 无配置视图"))
            return
        sub = msg.get("sub", "get")
        if sub == "get":
            Message.send(conn, Response.command_result(
                "plugin", None, action="config", config=config.as_dict(),
            ))
        elif sub == "set":
            key = msg.get("key", "")
            value = msg.get("value", "")
            if not isinstance(key, str) or not key:
                Message.send(conn, Response.error("key 必须为非空字符串"))
                return
            if len(value) > _MAX_CONFIG_VALUE_LEN:
                Message.send(conn, Response.error("value 过长"))
                return
            # 尝试 JSON 解析（支持 int/bool/array/object 等），失败保留原字符串
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                parsed = value
            try:
                config.set(key, parsed)
            except ConfigError as e:
                Message.send(conn, Response.error("配置设置失败: %s" % e))
                return
            Message.send(conn, Response.command_result(
                "plugin", None, action="config",
                message="配置已更新: %s.%s" % (name, key),
            ))
        else:
            Message.send(conn, Response.error("config sub 必须为 get 或 set"))