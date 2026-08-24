import traceback

from ...protocol.message import Message
from ...protocol.response import Response
from ...execution import (
    _run_snapshot_flow,
    _run_subprocess_no_trigger_flow,
    _run_subprocess_trigger_flow,
)
from .base import DaemonHandler
from ...execution.context import HandlerContext
from ...execution.utils import (
    apply_client_defaults,
    check_ended_session,
    validate_request,
    validate_trigger_regex,
)
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class SendHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        from ...config.common import MAX_INPUT_LEN, MAX_PATTERN_LEN, MAX_SESSION_ID_LEN

        session_id = msg.get("id", "")
        trigger = msg.get("trigger")
        if not validate_request(
            conn,
            msg,
            [
                ("id", MAX_SESSION_ID_LEN),
                ("input", MAX_INPUT_LEN),
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
                Message.send(
                    conn,
                    Response.error(
                        f"Session '{session_id}' not found",
                        suggest="Use 'app.py list' to see available sessions",
                    ),
                )
            return

        # 处理期间持有会话：会话可能在本 handler 等待输出期间自然结束，
        # 管理器会触发 release_components 释放大缓冲；hold 确保缓冲在
        # 响应构造完成前不被提前释放（最后一个 hold 退出时才实际释放）
        with session.hold():
            return self._handle_send_flow(ctx, conn, session, msg)

    def _handle_send_flow(self, ctx, conn, session, msg):
        """send 会话处理主体（已持有 session.hold）"""
        from ...execution.conditions import RequestContext

        if not apply_client_defaults(session, msg, conn):
            return

        req = RequestContext.from_msg(msg)
        cond = req.cond
        # 本流程独立于 handle() 从消息取值：会话 id 与输入文本供
        # 写入与日志使用（send 写入失败的日志定位不依赖会话对象）
        session_id = req.id
        input_text = req.input
        trigger = cond.trigger

        is_sub = getattr(session, "mode", "pty") == "subprocess"

        if not session.running:
            Message.send(
                conn,
                Response.error(
                    "Session has ended. Use 'read' to view remaining output, or 'events' to check pending events."
                ),
            )
            return

        if is_sub and cond.snapshot_diff:
            Message.send(
                conn,
                Response.error(
                    "子进程模式不支持 --snapshot-diff（无终端快照），请用增量输出"
                ),
            )
            return

        # 转义展开由守护进程统一完成（按会话模式决定 {enter}/默认行尾符）：
        # pty={enter}→\r, subprocess={enter}→\n；CLI 只透传原始 input + 转义开关 + 显式 eol
        from ...execution.utils import prepare_input

        # 转义解析失败（如不可识别的 {body} 控制序列）应返回明确错误而非内部 500
        try:
            input_text, pause_offsets = prepare_input(
                session.mode,
                input_text,
                json_escaping=req.json_escaping,
                send_eol=req.send_eol,
            )
        except ValueError as e:
            Message.send(conn, Response.error(str(e)))
            return

        try:
            session.write_input(input_text, pause_offsets=pause_offsets)
            _logger.info("会话 '%s' 输入: %s", session_id, repr(input_text[:100]))
        except Exception as e:
            tb = traceback.format_exc()
            _logger.error("会话 '%s' 写入失败: %s", session_id, e)
            _logger.error(tb)
            Message.send(conn, Response.error("Failed to write input"))
            return

        if is_sub:
            # 子进程模式：写 stdin，增量输出 + stderr
            if trigger:
                _run_subprocess_trigger_flow(
                    ctx,
                    conn,
                    session,
                    msg,
                    session.read_base(cond.full),
                    trigger,
                    cond.newline,
                    cond.fresh,
                    cond.timeout,
                    result_type="send",
                )
            else:
                _run_subprocess_no_trigger_flow(
                    ctx,
                    conn,
                    session,
                    msg,
                    result_type="send",
                    from_offset=session.read_base(cond.full),
                )
            return

        # pty 模式恒为屏幕快照，trigger/idle-timeout 由快照流程内部处理
        _run_snapshot_flow(ctx, conn, session, msg, result_type="send")
