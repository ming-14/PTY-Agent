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
                        "tag": list(s.tag),
                        # 会话模式/后端类型：前端据此判断子进程模式
                        # （无终端，输入直写 stdin），列表源头补齐避免
                        # 依赖 subscribe 响应时序。
                        # ActiveSession 摘要实体无 mode/pty_type 字段，
                        # 仅完整 Session 对象携带，缺失时回退默认值。
                        "mode": getattr(s, "mode", "pty"),
                        "ptyType": getattr(s, "pty_type", ""),
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
            # 顺带返回守护进程工作目录（前端新建会话对话框工作目录默认值）
            return [Response.ws_shell_list(available, cwd=ctx.shell_provider.default_cwd())]
        except Exception as e:
            _logger.warning("list_shells failed: %s", e)
            return [Response.ws_shell_list({})]


class SystemStatsHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        stats = await ctx.system_stats.get_stats()
        return [Response.ws_system_stats(stats.cpu, stats.memory)]