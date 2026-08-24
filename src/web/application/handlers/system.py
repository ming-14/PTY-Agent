"""系统信息处理器：ping / 会话列表 / shell 列表 / 系统状态。"""

from __future__ import annotations

from ....protocol.response import Response
from .base import HandlerContext, MessageHandler, _logger


class PingHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        return [Response.pong()]


class ListSessionsHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        sessions = ctx.session_repo.list_sessions()
        return [
            Response.ws_session_list(
                [
                    {
                        "id": s.id,
                        "uid": s.uid,
                        "command": s.command,
                        "running": s.running,
                        "startTime": s.start_time,
                    }
                    for s in sessions
                ]
            )
        ]


class ListShellsHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        try:
            shells = ctx.shell_provider.list_shells()
            available = {name: path for name, path in shells.items() if path}
            return [Response.ws_shell_list(available)]
        except Exception as e:
            _logger.warning("list_shells failed: %s", e)
            return [Response.ws_shell_list({})]


class SystemStatsHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        stats = await ctx.system_stats.get_stats()
        return [Response.ws_system_stats(stats.cpu, stats.memory)]