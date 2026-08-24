"""鼠标增强光标定位器处理器。"""

from __future__ import annotations

from ....protocol.response import Response
from .base import HandlerContext, MessageHandler, _logger


class CursorLocatorStartHandler(MessageHandler):
    """启动鼠标增强光标定位器。

    服务端单例：若已在运行，直接返回成功。
    启动操作通过 ThreadExecutor 调度避免阻塞事件循环。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.cursor_locator_service:
            return [Response.ws_cursor_locator_error("service unavailable", code="locator.service_unavailable")]
        if not ctx.cursor_locator_service.is_available():
            return [
                Response.ws_cursor_locator_error("cursor locator unavailable, Windows only", code="locator.windows_only")
            ]
        try:
            result = await ctx.executor.run(ctx.cursor_locator_service.start)
            if result.get("running"):
                _logger.info("CursorLocator started")
                return [Response.ws_cursor_locator_started()]
            return [Response.ws_cursor_locator_error("start failed", code="locator.start_failed", params={"error": result.get("error", "")})]
        except Exception as e:
            _logger.exception("CursorLocator start failed")
            return [Response.ws_cursor_locator_error("start failed", code="locator.start_failed", params={"error": str(e)})]


class CursorLocatorStopHandler(MessageHandler):
    """停止鼠标增强光标定位器。"""

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.cursor_locator_service:
            return [Response.ws_cursor_locator_error("service unavailable", code="locator.service_unavailable")]
        try:
            result = await ctx.executor.run(ctx.cursor_locator_service.stop)
            if not result.get("running"):
                _logger.info("CursorLocator stopped")
                return [Response.ws_cursor_locator_stopped()]
            return [Response.ws_cursor_locator_error("stop failed", code="locator.stop_failed", params={"error": result.get("error", "")})]
        except Exception as e:
            _logger.exception("CursorLocator stop failed")
            return [Response.ws_cursor_locator_error("stop failed", code="locator.stop_failed", params={"error": str(e)})]


class CursorLocatorUpdateConfigHandler(MessageHandler):
    """修改鼠标增强光标定位器配置参数。

    支持 outer_radius / inner_radius / alpha 三个参数，
    运行时实时生效（调用 cursorlocator.update_config），同时持久化到 JSON。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.cursor_locator_service:
            return [Response.ws_cursor_locator_error("service unavailable", code="locator.service_unavailable")]
        params = {}
        for key in ("outer_radius", "inner_radius", "alpha"):
            if key in msg:
                try:
                    params[key] = int(msg[key])
                except (TypeError, ValueError):
                    return [Response.ws_cursor_locator_error("invalid parameter", code="locator.invalid_param", params={"key": key})]
        if not params:
            return [Response.ws_cursor_locator_error("no parameters specified", code="locator.no_params")]
        try:
            result = await ctx.executor.run(
                ctx.cursor_locator_service.update_config, **params
            )
            if "error" in result:
                return [Response.ws_cursor_locator_error("update failed", code="locator.update_failed", params={"error": result["error"]})]
            _logger.info("CursorLocator config updated: %s", params)
            status = await ctx.executor.run(ctx.cursor_locator_service.get_status)
            return [Response.ws_cursor_locator_status(status)]
        except Exception as e:
            _logger.exception("CursorLocator update_config failed")
            return [Response.ws_cursor_locator_error("update failed", code="locator.update_failed", params={"error": str(e)})]