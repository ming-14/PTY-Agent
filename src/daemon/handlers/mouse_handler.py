import time
from typing import Optional

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler, HandlerContext
from .exec_handler import _run_snapshot_flow
from .utils import (
    apply_client_defaults,
    attach_screen_buffer,
    build_result,
    check_ended_session,
    format_iso_ms,
    strip_if_needed,
    validate_request,
    validate_trigger_regex,
)
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class MouseHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        from ...config.common import MAX_PATTERN_LEN, MAX_SESSION_ID_LEN

        session_id = msg.get("id", "")
        trigger = msg.get("trigger")
        if not validate_request(
            conn,
            msg,
            [
                ("id", MAX_SESSION_ID_LEN),
                ("grep", MAX_PATTERN_LEN),
                ("trigger", MAX_PATTERN_LEN),
            ],
        ):
            return
        if not validate_trigger_regex(trigger, conn):
            return

        if not session_id:
            Message.send(conn, Response.error("Missing session id"))
            return

        session = ctx.manager.get_session(session_id)
        if not session:
            if check_ended_session(ctx.manager, session_id):
                Message.send(
                    conn,
                    Response.error(
                        "Session has ended. Use 'read' to view remaining output, or 'events' to check pending events."
                    ),
                )
            else:
                Message.send(conn, Response.error(f"Session '{session_id}' not found"))
            return

        apply_client_defaults(session, msg)

        if not session.running:
            Message.send(
                conn,
                Response.error(
                    "Session has ended. Use 'read' to view remaining output, or 'events' to check pending events."
                ),
            )
            return

        if session.pty_type == "subprocess":
            _send_mouse_response(
                ctx,
                conn,
                session,
                session_id,
                msg,
                performed=False,
                error="Mouse actions require PTY mode (subprocess session has no terminal)",
            )
            return

        result = session.perform_mouse_action(msg)
        matches = result.get("matches")
        error = result.get("message")

        if not result.get("performed"):
            if msg.get("action") == "grep":
                _send_mouse_response(
                    ctx,
                    conn,
                    session,
                    session_id,
                    msg,
                    performed=False,
                    error=error,
                    matches=matches,
                    output="",
                    reason="ok",
                    has_trigger=False,
                    hint=error or ("grep completed" if matches else "No match found"),
                )
                return
            # pty 恒为快照
            keep_ansi = msg.get("keep_ansi", False)
            output = session.get_snapshot(keep_ansi=keep_ansi)
            if not keep_ansi:
                output = strip_if_needed(output, msg)
            stderr_output = ""
            resp_extra = {"performed": False}
            result_obj = build_result(
                ctx.manager,
                session_id,
                output,
                False,
                "ok",
                consume_events=True,
                has_trigger=False,
                result_type="mouse",
                session=session,
                t_start=msg.get("_t_start"),
            )
            result_obj.update(resp_extra)
            if matches is not None:
                result_obj["matches"] = matches
                # 多匹配未执行：hint 必须与 performed:false 一致，覆盖成功文案
                result_obj["hint"] = (
                    "Multiple matches found; please specify coordinates "
                    "or a more specific pattern"
                )
            if error:
                result_obj["message"] = error
            attach_screen_buffer(result_obj, session, msg)
            Message.send(conn, result_obj)
            return

        extra = {"performed": True}
        if msg.get("action") == "_get_cursor_location":
            cursor = result.get("cursor")
            _send_mouse_response(
                ctx,
                conn,
                session,
                session_id,
                msg,
                performed=True,
                hint=f"Cursor at col={cursor['col']} row={cursor['row']}"
                if cursor
                else "Cursor location unavailable",
                cursor=cursor,
            )
            return
        # pty 恒为快照，trigger/idle-timeout 由快照流程内部处理
        _run_snapshot_flow(
            ctx, conn, session, msg, result_type="mouse", extra_fields=extra
        )


def _send_mouse_response(
    ctx,
    conn,
    session,
    session_id: str,
    msg: dict,
    performed: bool,
    error: Optional[str] = None,
    matches: Optional[list] = None,
    output: Optional[str] = None,
    matched: bool = False,
    reason: str = "ok",
    has_trigger: bool = False,
    warning: Optional[str] = None,
    hint: Optional[str] = None,
    cursor: Optional[dict] = None,
):
    running = session.running if session else False
    exit_code = session.exit_code if session else None
    error_message = session.error_message if session else None

    if output is not None:
        resp = build_result(
            ctx.manager,
            session_id,
            output,
            matched,
            reason,
            consume_events=True,
            has_trigger=has_trigger,
            result_type="mouse",
            warning=warning,
            session=session,
            t_start=msg.get("_t_start"),
        )
    else:
        resp = {
            "commandType": "mouse",
            "sessionId": session_id,
            "performed": performed,
        }

    resp["performed"] = performed
    if matches is not None:
        resp["matches"] = matches
    if cursor is not None:
        resp["cursor"] = cursor
    if error:
        resp["message"] = error

    program = resp.get("program", {})
    program["rawStartCommand"] = session.command if session else None
    program["startTime"] = (
        format_iso_ms(session.start_time) if session and session.start_time else None
    )
    program["nowTime"] = format_iso_ms(time.time())
    program["running"] = running
    program["ptyType"] = session.pty_type if session else "none"
    if exit_code is not None:
        program["exitCode"] = exit_code
    if error_message is not None:
        program["errorMessage"] = error_message
    resp["program"] = program

    if hint is not None:
        resp["hint"] = hint
    elif performed:
        resp["hint"] = "Mouse action performed successfully"
    elif matches and not performed:
        resp["hint"] = (
            "Multiple matches found; please specify coordinates or a more specific pattern"
        )
    elif error:
        resp["hint"] = error
    else:
        resp["hint"] = "Mouse action failed"

    Message.send(conn, resp)
