import re
import time
import logging
from typing import Optional

from ...protocol.message import Message
from ...protocol.response import Response
from ...output import safe_regex_search
from .base import DaemonHandler, HandlerContext
from .exec_handler import _run_snapshot_flow, _run_trigger_flow, _run_no_trigger_flow
from .utils import (
    validate_request, apply_client_defaults, check_ended_session,
    build_result, attach_screen_buffer, strip_if_needed,
    filter_snapshot_lines, apply_lines_grep, format_iso_ms,
    _SESSION_ENDED_HINT,
)

_logger = logging.getLogger("pty-daemon")


class ReadHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        from ...config.common import MAX_SESSION_ID_LEN, MAX_PATTERN_LEN

        session_id = msg.get("id", "")
        lines_param = msg.get("lines")
        grep = msg.get("grep")
        offset = msg.get("offset")
        encoding = msg.get("encoding")
        trigger = msg.get("trigger")
        idle_timeout = msg.get("idle_timeout")
        explicit_timeout = msg.get("explicit_timeout", False)

        if not validate_request(conn, msg, [
            ("id", MAX_SESSION_ID_LEN),
            ("grep", MAX_PATTERN_LEN),
            ("trigger", MAX_PATTERN_LEN),
        ]):
            return

        if not session_id:
            Message.send(conn, Response.error("Missing session id"))
            return

        has_wait = trigger is not None or idle_timeout is not None or explicit_timeout

        if offset is not None and has_wait:
            Message.send(conn, Response.error("--offset cannot be used with --trigger/--idle-timeout/--timeout (waiting mode)"))
            return

        session = ctx.manager.get_session(session_id)
        if not session:
            hs = ctx.manager._history_store
            if hs:
                ended = hs.get_ended_output(session_id)
                if ended:
                    output = ended.get("output", "")
                    snapshot_text = ended.get("snapshot", "")
                    pty_type = ended.get("ptyType", "none")
                    if msg.get("snapshot"):
                        output = snapshot_text
                        if not msg.get("keep_ansi"):
                            output = strip_if_needed(output, {"keep_ansi": False})
                    else:
                        output = strip_if_needed(output, msg)
                    if msg.get("snapshot"):
                        output = filter_snapshot_lines(output, msg.get("lines"), msg.get("column"))
                    else:
                        output = apply_lines_grep(output, lines_param, grep, conn)
                        if output is None:
                            return
                    trigger_reason = "program_ended"
                    if trigger and output:
                        try:
                            if safe_regex_search(re.compile(trigger), output):
                                trigger_reason = "trigger_matched"
                        except re.error:
                            pass
                    result = {
                        "commandType": "read",
                        "sessionId": session_id,
                        "outputStream": output,
                        "outputOffset": 0,
                        "triggerReturnReason": trigger_reason,
                        "program": {
                            "rawStartCommand": ended.get("command"),
                            "startTime": format_iso_ms(ended.get("startTime")),
                            "nowTime": format_iso_ms(time.time()),
                            "running": False,
                            "ptyType": pty_type,
                        },
                        "hint": _SESSION_ENDED_HINT,
                    }
                    if ended.get("exitCode") is not None:
                        result["program"]["exitCode"] = ended["exitCode"]
                    if ended.get("errorMessage") is not None:
                        result["program"]["errorMessage"] = ended["errorMessage"]
                    t_start = msg.get("_t_start")
                    if t_start is not None:
                        result["program"]["debugInformation"] = {
                            "elapsedMs": round((time.monotonic() - t_start) * 1000, 3),
                        }
                    Message.send(conn, result)
                    return
            Message.send(conn, Response.error(f"Session '{session_id}' not found"))
            return

        apply_client_defaults(session, msg)

        snapshot = msg.get("snapshot", False) or session.snapshot_mode
        if msg.get("snapshot_diff") and not snapshot:
            Message.send(conn, Response.error("--snapshot-diff requires snapshot mode (session not in snapshot mode)"))
            return
        if offset is not None and lines_param is not None:
            Message.send(conn, Response.error("--offset cannot be used with --lines/-l"))
            return
        if offset is not None and msg.get("full"):
            Message.send(conn, Response.error("--offset cannot be used with --full"))
            return

        if has_wait:
            if snapshot:
                result, output = _run_snapshot_flow(
                    ctx, conn, session, msg, result_type="read",
                    send_response=False,
                )
                output = filter_snapshot_lines(output, lines_param, msg.get("column"))
            elif trigger:
                trigger_offset = 0 if msg.get("full") else session.output_offset
                result, output = _run_trigger_flow(
                    ctx, conn, session, msg, trigger_offset,
                    trigger, msg.get("newline", False),
                    msg.get("fresh", True), msg.get("timeout", 120),
                    result_type="read",
                    send_response=False,
                )
            else:
                result, output = _run_no_trigger_flow(
                    ctx, conn, session, msg, result_type="read",
                    send_response=False,
                )

            if not snapshot:
                output = apply_lines_grep(output, lines_param, grep, conn)
                if output is None:
                    return
                result["outputStream"] = output

            attach_screen_buffer(result, session, msg)
            Message.send(conn, result)
            return

        if snapshot:
            if msg.get("snapshot_diff"):
                output = session.get_snapshot_diff(keep_ansi=msg.get("keep_ansi", False))
            else:
                output = session.get_snapshot(keep_ansi=msg.get("keep_ansi", False))
            output = filter_snapshot_lines(output, lines_param, msg.get("column"))
            result = build_result(
                ctx.manager, session_id, output, False, "ended",
                has_trigger=False, result_type="read",
                session=session,
            )
            if not output:
                result["snapshotDiagnostics"] = session.get_snapshot_diagnostics()
            attach_screen_buffer(result, session, msg)
            Message.send(conn, result)
            return

        read_offset = offset
        if msg.get("full"):
            read_offset = 0

        if read_offset is None:
            output, cur_offset = session.get_output_with_offset(
                from_offset=session.output_offset, encoding=encoding)
        else:
            output, cur_offset = session.get_output_with_offset(
                from_offset=read_offset, encoding=encoding)
        output = strip_if_needed(output, msg)

        if read_offset is not None and not lines_param and not grep:
            result = build_result(
                ctx.manager, session_id, output, False, "ended",
                has_trigger=False, result_type="read",
                session=session,
                t_start=msg.get("_t_start"),
                output_offset=cur_offset,
            )
            attach_screen_buffer(result, session, msg)
            Message.send(conn, result)
            return

        lines = output.splitlines()

        if lines_param is not None:
            if isinstance(lines_param, int):
                lines = lines[-lines_param:] if lines_param > 0 else []
            elif isinstance(lines_param, str) and ":" in lines_param:
                parts = lines_param.split(":", 1)
                try:
                    start = int(parts[0]) if parts[0] else 1
                    end = int(parts[1]) if parts[1] else len(lines)
                    if start < 1:
                        start = 1
                    lines = lines[start - 1:end]
                except (ValueError, IndexError):
                    Message.send(conn, Response.error(f"Invalid line range: {lines_param}"))
                    return
            else:
                try:
                    n = int(lines_param)
                    lines = lines[-n:] if n > 0 else []
                except ValueError:
                    Message.send(conn, Response.error(f"Invalid lines parameter: {lines_param}"))
                    return

        if grep:
            try:
                pat = re.compile(grep)
                lines = [l for l in lines if safe_regex_search(pat, l)]
            except re.error:
                Message.send(conn, Response.error(f"Invalid regex: {grep}"))
                return

        column_param = msg.get("column")
        if column_param is not None and lines:
            col_idx = column_param - 1
            lines = [
                line[col_idx] if 0 <= col_idx < len(line) else ""
                for line in lines
            ]

        output = "\n".join(lines)
        result = build_result(
            ctx.manager, session_id, output, False, "ended",
            has_trigger=False, result_type="read",
            session=session,
            t_start=msg.get("_t_start"),
            output_offset=cur_offset,
        )
        attach_screen_buffer(result, session, msg)
        Message.send(conn, result)
