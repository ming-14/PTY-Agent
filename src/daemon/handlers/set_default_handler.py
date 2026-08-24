"""set-default / get-defaults 命令处理 — SetDefaultHandler

set-default 的默认配置为**守护进程内存记忆**（不写任何文件，daemon 重启即清空）：
- set_default 消息：校验 key/value → 写入 manager 全局默认 → 返回当前全部默认
- get_defaults 消息：返回当前全部默认（客户端 CLI 启动时拉取，合并到本地配置）
"""

from ...config.default_keys import normalize_default_value
from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler
from ...execution.context import HandlerContext
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class SetDefaultHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        key = msg.get("key")
        if not key:
            Message.send(conn, Response.error("key is required"))
            return
        internal_key = key.replace("-", "_")
        value = msg.get("value")
        try:
            value = normalize_default_value(internal_key, value)
        except ValueError as e:
            Message.send(conn, Response.error(str(e)))
            return
        ctx.manager.set_global_default(internal_key, value)
        _logger.info("set-default: %s = %r（守护进程内存）", internal_key, value)
        Message.send(
            conn,
            {
                "type": "set_default",
                "key": internal_key,
                "value": value,
                "defaults": ctx.manager.get_global_defaults(),
            },
        )


class GetDefaultsHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        Message.send(
            conn,
            {"type": "get_defaults", "defaults": ctx.manager.get_global_defaults()},
        )
