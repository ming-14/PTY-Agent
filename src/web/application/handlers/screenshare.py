"""FastScreen 屏幕查看处理器。"""

from __future__ import annotations

from ....config.common import IS_WINDOWS
from ....protocol.response import Response
from .base import HandlerContext, MessageHandler, _logger


class FsStatusHandler(MessageHandler):
    """查询 FastScreen 服务状态。

    返回 {type: fs_status, disabled, available, active_sessions}。
    FastScreen 未启用时返回 disabled=true，前端据此隐藏 UI 入口。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.screenshare_service:
            status = {
                "disabled": True,
                "available": False,
                "active_sessions": 0,
            }
        else:
            status = await ctx.executor.run(ctx.screenshare_service.get_status)
        if ctx.cursor_locator_service:
            cl_status = await ctx.executor.run(ctx.cursor_locator_service.get_status)
            status["cursor_locator_running"] = cl_status.get("running", False)
            status["cursor_locator_available"] = cl_status.get("available", False)
        else:
            status["cursor_locator_running"] = False
            status["cursor_locator_available"] = False
        return [Response.ws_fs_status(status)]


class FsListTargetsHandler(MessageHandler):
    """列出可查看目标（显示器 + 窗口）。

    前端打开 FastScreen tab 或点击"刷新"按钮时调用。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.screenshare_service:
            return [
                Response.ws_fs_targets(
                    {
                        "disabled": True,
                        "monitors": [],
                        "windows": [],
                    }
                )
            ]
        try:
            targets = await ctx.executor.run(ctx.screenshare_service.list_targets)
            return [Response.ws_fs_targets(targets)]
        except Exception as e:
            _logger.exception("FastScreen list_targets failed")
            return [Response.ws_fs_error("list targets failed", code="fs.list_targets_failed", params={"error": str(e)})]


class FsBringToFrontHandler(MessageHandler):
    """将指定窗口置于前台（恢复最小化 + 激活）。

    前端在窗口最小化提示中点击"置于前台"按钮时调用。
    仅 Windows 平台、窗口模式可用，使用 ShowWindowAsync(SW_RESTORE) + SetForegroundWindow。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not IS_WINDOWS:
            return [Response.ws_fs_error("bring to front supported on Windows only", code="fs.windows_only")]

        target_type = msg.get("target_type", "monitor")
        if target_type != "window":
            return [Response.ws_fs_error("bring to front supported in window mode only", code="fs.window_mode_only")]

        hwnd = msg.get("target_id", 0)
        try:
            hwnd = int(hwnd)
        except (TypeError, ValueError):
            return [Response.ws_fs_error("invalid window handle", code="fs.invalid_hwnd")]

        if hwnd == 0:
            return [Response.ws_fs_error("invalid window handle", code="fs.invalid_hwnd")]

        try:
            import ctypes

            user32 = ctypes.windll.user32
            SW_RESTORE = 9
            # 先恢复（如果最小化了），再置于前台
            user32.ShowWindowAsync(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            _logger.info("FastScreen bring window to front: hwnd=%d", hwnd)
            return []
        except Exception as e:
            _logger.exception("FastScreen bring to front failed: hwnd=%d", hwnd)
            return [Response.ws_fs_error("bring to front failed", code="fs.bring_to_front_failed", params={"error": str(e)})]