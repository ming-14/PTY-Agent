import time
import logging
from typing import Optional

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler, HandlerContext
from .exec_handler import _run_snapshot_flow, _run_trigger_flow, _run_no_trigger_flow
from .utils import (
    validate_request, apply_client_defaults, check_ended_session,
    build_result, attach_screen_buffer, strip_if_needed, format_iso_ms,
)

_logger = logging.getLogger("pty-daemon")


class MouseHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        from ...config.common import MAX_SESSION_ID_LEN, MAX_PATTERN_LEN

        session_id = msg.get("id", "")
        trigger = msg.get("trigger")
        if not validate_request(conn, msg, [
            ("id", MAX_SESSION_ID_LEN),
            ("grep", MAX_PATTERN_LEN),
            ("trigger", MAX_PATTERN_LEN),
        ]):
            return

        if not session_id:
            Message.send(conn, Response.error("Missing session id"))
            return

        session = ctx.manager.get_session(session_id)
        if not session:
            if check_ended_session(ctx.manager, session_id):
                Message.send(conn, Response.error("Session has ended. Use 'read' to view remaining output, or 'events' to check pending events."))
            else:
                Message.send(conn, Response.error(f"Session '{session_id}' not found"))
            return

        apply_client_defaults(session, msg)

        if not session.running:
            Message.send(conn, Response.error("Session has ended. Use 'read' to view remaining output, or 'events' to check pending events."))
            return

        if session.pty_type == "subprocess":
            output = session.get_output(encoding=msg.get("encoding"))
            output = strip_if_needed(output, msg)
            stderr_output = session.get_stderr_output(encoding=msg.get("encoding"))
            _send_mouse_response(
                ctx, conn, session, session_id, msg, performed=False,
                error="Mouse actions require PTY mode", output=output,
                stderr_output=stderr_output,
            )
            return

        if not session.snapshot_mode:
            _send_mouse_response(
                ctx, conn, session, session_id, msg, performed=False,
                error="Mouse actions require snapshot mode (--snapshot-mode for exec; --snapshot for send/read; or --default always-return-snapshot on)",
            )
            return

        result = session.perform_mouse_action(msg)
        matches = result.get("matches")
        error = result.get("message")

        if not result.get("performed"):
            if msg.get("action") == "grep":
                _send_mouse_response(
                    ctx, conn, session, session_id, msg, performed=False,
                    error=error, matches=matches, output="",
                    stderr_output="", reason="ok", has_trigger=False,
                    hint=error or ("grep completed" if matches else "No match found"),
                )
                return
            if session.snapshot_mode or msg.get("snapshot"):
                keep_ansi = msg.get("keep_ansi", False)
                output = session.get_snapshot(keep_ansi=keep_ansi)
                if not keep_ansi:
                    output = strip_if_needed(output, msg)
            else:
                output = session.get_output(encoding=msg.get("encoding"))
                output = strip_if_needed(output, msg)
            stderr_output = ""
            resp_extra = {"performed": False}
            result_obj = build_result(
                ctx.manager, session_id, output, False, "ok",
                consume_events=True, has_trigger=False,
                result_type="mouse", session=session,
                t_start=msg.get("_t_start"),
            )
            result_obj.update(resp_extra)
            if matches is not None:
                result_obj["matches"] = matches
            if error:
                result_obj["message"] = error
            if session.snapshot_mode or msg.get("snapshot"):
                attach_screen_buffer(result_obj, session, msg)
            Message.send(conn, result_obj)
            return

        extra = {"performed": True}
        if msg.get("action") == "_get_cursor_location":
            cursor = result.get("cursor")
            _send_mouse_response(
                ctx, conn, session, session_id, msg, performed=True,
                hint=f"Cursor at col={cursor['col']} row={cursor['row']}" if cursor else "Cursor location unavailable",
                cursor=cursor,
            )
            return
        if session.snapshot_mode or msg.get("snapshot"):
            _run_snapshot_flow(ctx, conn, session, msg, result_type="mouse", extra_fields=extra)
        elif trigger:
            trigger_offset = 0 if msg.get("full") else session.output_offset
            _run_trigger_flow(
                ctx, conn, session, msg, trigger_offset,
                trigger, msg.get("newline", False),
                msg.get("fresh", False), msg.get("timeout", 120),
                result_type="mouse", extra_fields=extra,
            )
        else:
            _run_no_trigger_flow(ctx, conn, session, msg, result_type="mouse", extra_fields=extra)


def _send_mouse_response(
    ctx, conn, session, session_id: str, msg: dict,
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
            ctx.manager, session_id, output, matched, reason,
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
    program["startTime"] = format_iso_ms(session.start_time) if session and session.start_time else None
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
        resp["hint"] = "Multiple matches found; please specify coordinates or a more specific pattern"
    elif error:
        resp["hint"] = error
    else:
        resp["hint"] = "Mouse action failed"

    Message.send(conn, resp)
