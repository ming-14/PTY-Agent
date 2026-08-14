"""plugin 命令处理 — 插件管理（list/ls/attach/detach/cmd）

消息格式:
    {"type": "plugin", "action": "list"}
    {"type": "plugin", "action": "ls",      "id": "<sid>"}
    {"type": "plugin", "action": "attach",  "id": "<sid>", "name": "<plugin>"}
    {"type": "plugin", "action": "detach",  "id": "<sid>", "name": "<plugin>"}
    {"type": "plugin", "action": "cmd",     "id": "<sid>", "name": "<plugin>",
     "command": "<cmd>", "args": [...]}

attach/detach 作用于运行中的会话；list 列出进程级已加载插件；
cmd 路由到会话挂载插件的 handle_command（未处理返回错误）。
"""

import logging

from ...config.common import MAX_COMMAND_LEN, MAX_SESSION_ID_LEN
from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler, HandlerContext
from .utils import validate_field

_logger = logging.getLogger("pty-daemon")

_MAX_PLUGIN_NAME_LEN = 64


class PluginHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        action = msg.get("action", "")
        session_id = msg.get("id", "")
        name = msg.get("name", "")

        if action in ("attach", "detach", "cmd"):
            if not validate_field(name, "name", _MAX_PLUGIN_NAME_LEN, conn):
                return
        if action in ("ls", "attach", "detach", "cmd"):
            if not validate_field(session_id, "id", MAX_SESSION_ID_LEN, conn):
                return

        if action == "list":
            self._handle_list(ctx, conn)
        elif action == "ls":
            self._handle_ls(ctx, conn, session_id)
        elif action == "attach":
            self._handle_attach(ctx, conn, session_id, name)
        elif action == "detach":
            self._handle_detach(ctx, conn, session_id, name)
        elif action == "cmd":
            self._handle_cmd(ctx, conn, msg, session_id, name)
        else:
            Message.send(conn, Response.error(f"未知 plugin action: {action}"))

    def _handle_list(self, ctx: HandlerContext, conn):
        registry = ctx.manager.plugin_registry
        if registry is None:
            Message.send(conn, Response.error("插件系统未启用"))
            return
        plugins = registry.list_all()
        Message.send(
            conn,
            Response.command_result("plugin", None, action="list", plugins=plugins),
        )

    def _handle_ls(self, ctx: HandlerContext, conn, session_id: str):
        session = ctx.manager.get_session(session_id)
        if session is None:
            Message.send(conn, Response.error(f"会话 '{session_id}' 不存在"))
            return
        plugins = session.plugin_host.snapshot_info() or []
        Message.send(
            conn,
            Response.command_result("plugin", session_id, action="ls", plugins=plugins),
        )

    def _handle_attach(self, ctx: HandlerContext, conn, session_id: str, name: str):
        session = ctx.manager.get_session(session_id)
        if session is None:
            Message.send(conn, Response.error(f"会话 '{session_id}' 不存在"))
            return
        if not session.running:
            Message.send(conn, Response.error(f"会话 '{session_id}' 已结束"))
            return
        registry = ctx.manager.plugin_registry
        if registry is None:
            Message.send(conn, Response.error("插件系统未启用"))
            return
        inst = registry.instantiate(name)
        if inst is None:
            Message.send(conn, Response.error(f"插件未加载: {name}"))
            return
        if not session.plugin_host.attach(inst):
            Message.send(conn, Response.error(f"插件已挂载: {name}"))
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

    def _handle_detach(self, ctx: HandlerContext, conn, session_id: str, name: str):
        session = ctx.manager.get_session(session_id)
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

    def _handle_cmd(
        self, ctx: HandlerContext, conn, msg: dict, session_id: str, name: str
    ):
        command = msg.get("command", "")
        if not validate_field(command, "command", MAX_COMMAND_LEN, conn):
            return
        session = ctx.manager.get_session(session_id)
        if session is None:
            Message.send(conn, Response.error(f"会话 '{session_id}' 不存在"))
            return
        result = session.plugin_host.handle_command(name, msg)
        if result is None:
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
