import traceback
import logging

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler, HandlerContext
from .exec_handler import _run_snapshot_flow, _run_trigger_flow, _run_no_trigger_flow
from .utils import validate_request, apply_client_defaults, check_ended_session

_logger = logging.getLogger("pty-daemon")


class SendHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        from ...config.common import MAX_SESSION_ID_LEN, MAX_INPUT_LEN, MAX_PATTERN_LEN

        session_id = msg.get("id", "")
        input_text = msg.get("input", "")
        trigger = msg.get("trigger")
        if not validate_request(conn, msg, [
            ("id", MAX_SESSION_ID_LEN),
            ("input", MAX_INPUT_LEN),
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
                Message.send(conn, Response.error(f"Session '{session_id}' not found", suggest="Use 'app.py list' to see available sessions"))
            return

        apply_client_defaults(session, msg)

        if not session.running:
            Message.send(conn, Response.error("Session has ended. Use 'read' to view remaining output, or 'events' to check pending events."))
            return

        if trigger:
            trigger_offset = 0 if msg.get("full") else session.output_offset
            session.set_trigger(trigger, newline=msg.get("newline", False),
                                fresh=msg.get("fresh", False))
            _logger.info("send trigger: id=%r trigger=%r offset=%d bufsize=%d",
                         session_id, trigger, trigger_offset, session.output_offset)

        try:
            session.write_input(input_text)
            _logger.info("会话 '%s' 输入: %s", session_id, repr(input_text[:100]))
        except Exception as e:
            tb = traceback.format_exc()
            _logger.error("会话 '%s' 写入失败: %s", session_id, e)
            _logger.error(tb)
            Message.send(conn, Response.error("Failed to write input"))
            return

        if session.snapshot_mode or msg.get("snapshot"):
            _run_snapshot_flow(ctx, conn, session, msg, result_type="send")
        elif trigger:
            _run_trigger_flow(
                ctx, conn, session, msg, trigger_offset,
                trigger, msg.get("newline", False),
                msg.get("fresh", False), msg.get("timeout", 120),
                result_type="send",
            )
        else:
            _run_no_trigger_flow(ctx, conn, session, msg, result_type="send")
