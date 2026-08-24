"""VNC 远程桌面处理器。"""

from __future__ import annotations

from ....protocol.response import Response
from .base import HandlerContext, MessageHandler, _logger


class VncStatusHandler(MessageHandler):
    """查询 VNC 服务状态。

    返回 {type: vnc_status, running, disabled, winvnc_available, ...}。
    VNC 未启用时返回 disabled=true，前端据此隐藏 UI 入口。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.vnc_service:
            return [
                Response.ws_vnc_status(
                    {
                        "running": False,
                        "disabled": True,
                        "winvnc_available": False,
                        "vnc_port": None,
                        "password": None,
                    }
                )
            ]
        status = await ctx.executor.run(ctx.vnc_service.get_status)
        return [Response.ws_vnc_status(status)]


class VncStartHandler(MessageHandler):
    """启动 VNC 服务（按需启动 winvnc.exe）。

    单例语义：若已在运行，直接返回当前连接信息。
    启动是同步阻塞操作（最多 30 秒），通过 ThreadExecutor 调度避免阻塞事件循环。
    WebSocket→VNC TCP 代理由守护进程 /vnc/websockify 端点实现，无需 websockify。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.vnc_service:
            return [Response.ws_vnc_error("VNC service not available", code="vnc.service_unavailable")]
        if not ctx.vnc_service.is_available():
            return [Response.ws_vnc_error("VNC unavailable", code="vnc.unavailable")]
        try:
            connection_info = await ctx.executor.run(ctx.vnc_service.start)
            _logger.info(
                "VNC started: vnc_port=%s",
                connection_info.get("vnc_port"),
            )
            return [Response.ws_vnc_started(connection_info)]
        except Exception as e:
            _logger.exception("VNC start failed")
            return [Response.ws_vnc_error("VNC start failed", code="vnc.start_failed", params={"error": str(e)})]


class VncStopHandler(MessageHandler):
    """停止 VNC 服务。"""

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.vnc_service:
            return [Response.ws_vnc_error("VNC service not available", code="vnc.service_unavailable")]
        try:
            await ctx.executor.run(ctx.vnc_service.stop)
            _logger.info("VNC stopped")
            return [Response.ws_vnc_stopped()]
        except Exception as e:
            _logger.exception("VNC stop failed")
            return [Response.ws_vnc_error("VNC stop failed", code="vnc.stop_failed", params={"error": str(e)})]