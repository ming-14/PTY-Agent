"""历史记录处理器：历史列表 / 详情 / 删除。

历史记录以 uid 为主键（同名 sid 会话可保留多条历史）；
入站消息优先 sessionUid，否则按 session_id(sid) 查找（兼容旧前端）。
"""

from __future__ import annotations

from ....protocol.response import Response
from .base import HandlerContext, MessageHandler


class ListHistoryHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.history_repo:
            return [Response.ws_history_list([])]
        sessions = ctx.history_repo.list_sessions()
        return [
            Response.ws_history_list(
                [
                    {
                        "id": s.id,
                        "uid": s.uid,
                        "command": s.command,
                        "ptyType": s.pty_type,
                        "encoding": s.encoding,
                        "startTime": s.start_time,
                        "endTime": s.end_time,
                        "exitCode": s.exit_code,
                        "errorMessage": s.error_message,
                        "running": False,
                        "tag": list(s.tag),
                    }
                    for s in sessions
                ]
            )
        ]


class HistoryDetailHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.history_repo:
            return [Response.error("no history store")]
        # 优先 uid，其次 sid（HistoryStore 双键查找）
        identifier = msg.get("sessionUid", "") or msg.get("session_id", "")
        detail = ctx.history_repo.get_session_detail(identifier)
        if not detail:
            return [Response.error(f"session '{identifier}' not found in history")]
        payload = {
            "type": "history_detail",
            "id": detail.id,
            "uid": detail.uid,
            "command": detail.command,
            "ptyType": detail.pty_type,
            "cols": detail.cols,
            "rows": detail.rows,
            "encoding": detail.encoding,
            "startTime": detail.start_time,
            "endTime": detail.end_time,
            "exitCode": detail.exit_code,
            "errorMessage": detail.error_message,
            "running": False,
            "replay": detail.replay,
            "snapshot": detail.snapshot,
        }
        if detail.screen_buffer_z:
            payload["screenBufferZ"] = detail.screen_buffer_z
            payload["screenBufferMeta"] = detail.screen_buffer_meta
        if detail.output_gz:
            payload["outputGz"] = detail.output_gz
            payload["outputGzOriginalLen"] = detail.output_gz_original_len
        if detail.events:
            payload["events"] = detail.events
        return [payload]


class DeleteHistoryHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        identifier = msg.get("sessionUid", "") or msg.get("session_id", "")
        if ctx.history_repo:
            ctx.history_repo.delete_session(identifier)
        return [Response.ws_history_deleted(identifier)]