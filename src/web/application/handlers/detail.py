"""会话详情处理器：活动会话详情 / 详情刷新。"""

from __future__ import annotations

from typing import Any

from ....protocol.response import Response
from ...domain.entities import SessionDetail
from .base import HandlerContext, MessageHandler, _command_to_string, _logger, _resolve_session_uid


class SessionDetailHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_uid = _resolve_session_uid(ctx, msg)
        session = ctx.session_repo.get_by_uid(session_uid) if session_uid else None
        if not session:
            if ctx.history_repo:
                # 历史会话：按 uid 或 sid 查找
                identifier = session_uid or msg.get("session_id", "")
                detail = ctx.history_repo.get_session_detail(identifier)
                if not detail and not session_uid:
                    # 兼容旧前端：按 sid 直接查找
                    detail = ctx.history_repo.get_session_detail(msg.get("session_id", ""))
                if detail:
                    payload = {
                        "type": "session_detail",
                        "source": "history",
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
            return [Response.error(f"session not found")]

        detail = await self._build_active_detail(ctx, session)
        return [self._detail_to_message(detail, source="active")]

    async def _build_active_detail(
        self, ctx: HandlerContext, session: Any
    ) -> SessionDetail:
        from ....process.info import (
            _get_process_tree,
        )

        command = _command_to_string(session.command)
        pids = []
        try:
            pids = await ctx.executor.run(lambda: session.get_pty_process_list())
        except Exception:
            pass
        pids = [p for p in pids if p > 0]

        child_pid = None
        try:
            child_pid = await ctx.executor.run(lambda: session.get_pty_child_pid())
        except Exception:
            pass

        process_tree, process_details_raw = await ctx.executor.run(
            _get_process_tree, pids, root_pid=child_pid or 0
        )
        process_details = {str(pid): d for pid, d in process_details_raw.items()}
        events = await ctx.executor.run(session.get_all_events)
        gui_windows = session.gui_windows

        return SessionDetail(
            id=session.id,
            uid=session.uid,
            command=command,
            pty_type=session.pty_type,
            cols=session.cols,
            rows=session.rows,
            encoding=session.encoding or "utf-8",
            start_time=session.start_time,
            running=session.running,
            exit_code=session.exit_code,
            error_message=session.error_message,
            cwd=session.cwd or "",
            process_tree=process_tree,
            process_details=process_details,
            events=events,
            gui_windows=gui_windows,
            output_size=session.output_offset,
        )

    @staticmethod
    def _detail_to_message(detail: SessionDetail, source: str) -> dict:
        return {
            "type": "session_detail",
            "source": source,
            "id": detail.id,
            "uid": detail.uid,
            "command": detail.command,
            "ptyType": detail.pty_type,
            "cols": detail.cols,
            "rows": detail.rows,
            "encoding": detail.encoding,
            "startTime": detail.start_time,
            "running": detail.running,
            "exitCode": detail.exit_code,
            "errorMessage": detail.error_message,
            "cwd": detail.cwd,
            "processTree": detail.process_tree,
            "processDetails": detail.process_details,
            "events": detail.events,
            "guiWindows": detail.gui_windows,
            "outputSize": detail.output_size,
        }


class SessionDetailRefreshHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_uid = _resolve_session_uid(ctx, msg)
        if not session_uid:
            return []
        session = ctx.session_repo.get_by_uid(session_uid)
        if not session:
            return []
        tab = msg.get("tab", "info")
        result = {"type": "session_detail_refresh", "id": session.id, "sessionUid": session_uid, "tab": tab}
        if tab == "info":
            result.update(
                {
                    "running": session.running,
                    "exitCode": session.exit_code,
                    "errorMessage": session.error_message,
                    "outputSize": session.output_offset,
                }
            )
        elif tab == "process":
            from ....process.info import _get_process_detail

            pids = []
            try:
                pids = await ctx.executor.run(lambda: session.get_pty_process_list())
            except Exception:
                pass
            pids = [p for p in pids if p > 0]
            process_details = {}
            for pid in pids:
                d = await ctx.executor.run(_get_process_detail, pid)
                if d:
                    process_details[str(pid)] = {
                        "pid": d["pid"],
                        "memoryMb": d.get("memoryMb"),
                        "cpuSeconds": d.get("cpuSeconds"),
                    }
            result["processDetails"] = process_details
        return [result]