"""notice 命令处理器 — 按 nid 查看通知的完整响应内容

通知的完整响应（build_result 产物）与普通命令回复结构一致
（commandType/sessionId/outputStream/triggerReturnReason/program 等），
直接通过 Message.send 发送，走正常信封包装+签名路径。

通知只读不消费：多次 notice 查看同一条通知都返回同一内容。
"""

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler
from ...execution.context import HandlerContext
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class NoticeHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        nid = msg.get("nid", "")
        if not nid:
            Message.send(conn, Response.error("Missing notification nid"))
            return

        notify_mgr = getattr(getattr(ctx, "server", None), "notify_manager", None)
        if notify_mgr is None:
            Message.send(conn, Response.error("Notification system not available"))
            return

        response = notify_mgr.get_by_nid(nid)
        if response is None:
            Message.send(
                conn,
                Response.error(f"Notification '{nid}' not found"),
            )
            return

        # 附加 noticeNid 顶层字段，供 presenter 展示（不影响 from_response 的 SessionResult 路径）
        response["noticeNid"] = nid
        Message.send(conn, response)
        _logger.info("notice: nid=%s session=%s reason=%r", nid,
                     response.get("sessionId"),
                     response.get("triggerReturnReason"))