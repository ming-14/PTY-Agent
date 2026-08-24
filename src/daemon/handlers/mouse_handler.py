import time
from typing import Optional

from ...protocol.message import Message
from ...protocol.reasons import Reason
from ...protocol.response import Response
from .base import DaemonHandler
from ...execution.context import HandlerContext
from .exec_handler import _run_snapshot_flow
from ...execution.filtering import strip_if_needed
from ...execution.response import (
    attach_screen_buffer,
    build_result,
    describe_output_format,
    format_iso_ms,
)
from ...execution.utils import (
    apply_client_defaults,
    check_ended_session,
    validate_request,
    validate_trigger_regex,
)
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class MouseHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        from ...config.common import MAX_PATTERN_LEN, MAX_SESSION_ID_LEN
        from ...execution.conditions import RequestContext

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

        if not apply_client_defaults(session, msg, conn):
            return

        if not session.running:
            Message.send(
                conn,
                Response.error(
                    "Session has ended. Use 'read' to view remaining output, or 'events' to check pending events."
                ),
            )
            return

        req = RequestContext.from_msg(msg)
        cond = req.cond

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
            if req.action == "grep":
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
                    reason=Reason.OK,
                )
                return
            # pty 恒为快照
            output = session.get_snapshot(keep_ansi=cond.keep_ansi)
            if not cond.keep_ansi:
                output = strip_if_needed(output, msg)
            resp_extra = {"performed": False}
            result_obj = build_result(
                ctx.manager,
                session_id,
                output,
                False,
                Reason.OK,
                consume_events=True,
                result_type="mouse",
                session=session,
                t_start=req.t_start,
            )
            result_obj.update(resp_extra)
            result_obj["format"] = describe_output_format(msg)
            # 供呈现层重建命中列表前缀与"未执行"消息判定
            result_obj["action"] = msg.get("action")
            result_obj["grep"] = msg.get("grep")
            if matches is not None:
                result_obj["matches"] = matches
            if error:
                result_obj["message"] = error
            attach_screen_buffer(result_obj, session, msg)
            Message.send(conn, result_obj)
            return

        extra = {"performed": True}
        if req.action == "_get_cursor_location":
            cursor = result.get("cursor")
            _send_mouse_response(
                ctx,
                conn,
                session,
                session_id,
                msg,
                performed=True,
                # 光标定位结果是正文（cursor 字段），不走 hint/hit 前缀
                hint="" if cursor else "Cursor location unavailable",
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
    reason: Reason = Reason.OK,
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
            "triggerReturnReason": reason,
            "format": describe_output_format(msg),
        }

    resp["performed"] = performed
    if resp.get("format") is None:
        resp["format"] = describe_output_format(msg)
    # 供呈现层重建命中列表前缀与"未执行"消息判定
    resp["action"] = msg.get("action")
    resp["grep"] = msg.get("grep")
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
    program["mode"] = getattr(session, "mode", "pty") if session else "pty"
    if exit_code is not None:
        program["exitCode"] = exit_code
    if error_message is not None:
        program["errorMessage"] = error_message
    resp["program"] = program

    if hint is not None:
        # 显式 hint 仅在需要透传补充信息时提供（如光标不可用），
        # 其余成功/失败/多匹配文案由呈现层按 performed/matches/error 数据重建
        resp["hint"] = hint

    Message.send(conn, resp)
