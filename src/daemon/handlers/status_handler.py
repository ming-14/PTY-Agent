import os
import time

from ...config.daemon import WEB_HOST, WEB_PORT
from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler, HandlerContext
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class StatusHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        sessions = ctx.manager.list_sessions()
        active = len(sessions)
        ended = 0
        hs = ctx.manager._history_store
        if hs:
            ended = len(hs.list_ended_sessions())
        uptime = (
            time.time() - ctx.server._start_time
            if ctx.server and hasattr(ctx.server, "_start_time")
            else None
        )
        # 多监听器架构下无单一 daemon 端口，取首个启用监听器的端口呈现
        port = None
        if ctx.server and hasattr(ctx.server, "listeners_config"):
            for _name, (enabled, _host, p) in ctx.server.listeners_config.items():
                if enabled:
                    port = p
                    break
        resp = Response.status_result(
            running=True,
            pid=os.getpid(),
            port=port,
            uptime=round(uptime, 1) if uptime is not None else None,
            active_sessions=active,
            ended_sessions=ended,
            web_url=f"http://{WEB_HOST}:{WEB_PORT}/",
        )
        Message.send(conn, resp)
