import re
import time

from ...output import safe_regex_search
from ...protocol.message import Message
from ...protocol.response import Response
from ..execution import (
    _run_snapshot_flow,
    _run_subprocess_no_trigger_flow,
    _run_subprocess_trigger_flow,
    assemble_response,
)
from .base import DaemonHandler, HandlerContext
from .utils import (
    _SESSION_ENDED_HINT,
    apply_client_defaults,
    apply_lines_grep,
    attach_screen_buffer,
    filter_snapshot_lines,
    format_iso_ms,
    resolve_output,
    strip_if_needed,
    validate_offset_policy,
    validate_request,
    validate_trigger_regex,
)
from ...logging import get_logger

_logger = get_logger("pty-daemon")


def _include_debug(session) -> bool:
    """read 非等待路径是否产出 debugInformation（--debug-output 经 client_defaults 落 session.client_config）"""
    return bool(
        getattr(session, "client_config", None) and session.client_config.get("debug")
    )


class ReadHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        from ...config.common import MAX_PATTERN_LEN, MAX_SESSION_ID_LEN

        session_id = msg.get("id", "")
        lines_param = msg.get("lines")
        grep = msg.get("grep")
        offset = msg.get("offset")
        encoding = msg.get("encoding")
        trigger = msg.get("trigger")
        idle_timeout = msg.get("idle_timeout")
        explicit_timeout = msg.get("explicit_timeout", False)

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

        has_wait = trigger is not None or idle_timeout is not None or explicit_timeout

        if not validate_offset_policy(conn, offset, waiting=has_wait):
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
                    # pty 恒为快照；子进程用增量输出
                    if pty_type == "subprocess":
                        stderr_text = strip_if_needed(
                            ended.get("stderrOutput", ""), msg
                        )
                        output = strip_if_needed(output, msg)
                        output = apply_lines_grep(
                            output, lines_param, grep, conn,
                            column_param=msg.get("column"),
                        )
                        if output is None:
                            return
                    else:
                        stderr_text = ""
                        output = snapshot_text
                        if not msg.get("keep_ansi"):
                            output = strip_if_needed(output, {"keep_ansi": False})
                        output = filter_snapshot_lines(
                            output, msg.get("lines"), msg.get("column"), grep
                        )
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
                    if pty_type == "subprocess" and stderr_text:
                        result["stderrOutput"] = stderr_text
                        # 与增量路径 stderrOutputOffset（字节偏移）语义一致：
                        # 已结束会话返回全部 stderr，偏移即全量 stderr 的字节长度
                        _enc = ended.get("encoding") or "utf-8"
                        result["stderrOutputOffset"] = len(
                            stderr_text.encode(_enc, errors="replace")
                        )
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

        # 处理期间持有会话：会话可能在本 handler 等待输出期间自然结束，
        # 管理器会触发 release_components 释放大缓冲；hold 确保缓冲在
        # 响应构造完成前不被提前释放（最后一个 hold 退出时才实际释放）
        with session.hold():
            return self._handle_read_flow(ctx, conn, session, msg)

    def _handle_read_flow(self, ctx, conn, session, msg):
        """read 会话处理主体（已持有 session.hold）"""
        from ..conditions import RequestContext

        if not apply_client_defaults(session, msg, conn):
            return

        req = RequestContext.from_msg(msg)
        cond = req.cond
        session_id = session.id
        lines_param = req.lines
        grep = req.grep
        offset = req.offset
        has_wait = cond.has_wait

        is_sub = getattr(session, "mode", "pty") == "subprocess"
        if is_sub:
            if cond.snapshot_diff:
                Message.send(
                    conn,
                    Response.error(
                        "子进程模式不支持 --snapshot-diff（无终端快照），请用增量输出"
                    ),
                )
                return
            self._handle_subprocess_read(ctx, conn, session, msg)
            return

        # pty 模式恒为屏幕快照
        if not validate_offset_policy(
            conn,
            offset,
            lines=lines_param,
            full=cond.full,
            snapshot_diff=cond.snapshot_diff,
        ):
            return

        if has_wait:
            # --lines 需作用于含 scrollback 历史的全量内容（隐式 full）
            wait_msg = msg
            if (
                lines_param is not None
                and not cond.full
                and not cond.snapshot_diff
            ):
                wait_msg = dict(msg)
                wait_msg["full"] = True
            result, output = _run_snapshot_flow(
                ctx,
                conn,
                session,
                wait_msg,
                result_type="read",
                send_response=False,
            )
            output = filter_snapshot_lines(
                output, lines_param, req.column, grep
            )
            # 过滤结果必须写回响应：build_result 装配的是未过滤快照，
            # 此前漏写回导致等待模式下 -g/-l/--column 全部静默失效
            result["outputStream"] = output
            attach_screen_buffer(result, session, msg)
            Message.send(conn, result)
            return

        # 非等待：直接返回快照（--full/--lines 时含 scrollback 历史）
        # 显式 --offset：与子进程模式语义一致，从原始输出缓冲区增量读取，
        # 返回"该偏移之后的新增输出"，响应 outputOffset 为缓冲结束偏移。
        # （offset 未显式给定时保持快照语义，read 默认返回可见屏幕快照）
        if offset is not None:
            output, cur_offset = session.get_output_with_offset(
                from_offset=offset, encoding=req.encoding
            )
            output = strip_if_needed(output, msg)
            if grep or req.column is not None:
                filtered = apply_lines_grep(
                    output, None, grep, conn,
                    column_param=req.column,
                )
                if filtered is None:
                    return
                output = filtered
            assemble_response(
                ctx,
                conn,
                session,
                msg,
                output=output,
                matched=False,
                reason="ok" if session.running else "ended",
                result_type="read",
                has_trigger=False,
                consume_events=False,
                output_offset=cur_offset,
                include_debug=_include_debug(session),
            )
            return

        output = resolve_output(
            session, cond, force_full=(lines_param is not None)
        )
        output = filter_snapshot_lines(output, lines_param, req.column, grep)
        assemble_response(
            ctx,
            conn,
            session,
            msg,
            output=output,
            matched=False,
            reason="ok" if session.running else "ended",
            result_type="read",
            has_trigger=False,
            consume_events=False,
            include_debug=_include_debug(session),
            snapshot_diagnostics=True,
        )

    def _handle_subprocess_read(self, ctx, conn, session, msg: dict):
        """子进程模式 read：增量读取 stdout + 附加 stderr

        支持 --offset / --full / -l / -g / --timeout / --idle-timeout / --trigger。
        """
        from ..conditions import RequestContext
        from .utils import attach_screen_buffer, strip_if_needed

        req = RequestContext.from_msg(msg)
        cond = req.cond
        lines_param = req.lines
        grep = req.grep
        offset = req.offset
        encoding = req.encoding
        trigger = cond.trigger
        has_wait = cond.has_wait

        if not validate_offset_policy(conn, offset, waiting=has_wait):
            return

        if has_wait:
            if trigger:
                trigger_offset = 0 if cond.full else session.output_offset
                result, output = _run_subprocess_trigger_flow(
                    ctx,
                    conn,
                    session,
                    msg,
                    trigger_offset,
                    trigger,
                    cond.newline,
                    # read 子进程 trigger 语义：fresh 默认 True（对齐 CLI 读取新输出）
                    msg.get("fresh", True),
                    cond.timeout,
                    result_type="read",
                    send_response=False,
                )
            else:
                result, output = _run_subprocess_no_trigger_flow(
                    ctx,
                    conn,
                    session,
                    msg,
                    result_type="read",
                    send_response=False,
                    from_offset=(
                        0 if cond.full else session.output_offset
                    ),
                )
            output = apply_lines_grep(
                output, lines_param, grep, conn,
                column_param=req.column,
            )
            if output is None:
                return
            result["outputStream"] = output
            attach_screen_buffer(result, session, msg)
            Message.send(conn, result)
            return

        # 非等待：增量读取 stdout
        if cond.full:
            read_offset = 0
        elif offset is not None:
            read_offset = offset
        else:
            read_offset = session.output_offset

        output, cur_offset = session.get_output_with_offset(
            from_offset=read_offset, encoding=encoding
        )
        output = strip_if_needed(output, msg)

        if (
            read_offset is not None
            and not lines_param
            and not grep
            and req.column is None
        ):
            assemble_response(
                ctx,
                conn,
                session,
                msg,
                output=output,
                matched=False,
                reason="ok" if session.running else "ended",
                result_type="read",
                has_trigger=False,
                consume_events=False,
                output_offset=cur_offset,
                include_debug=_include_debug(session),
                attach_stderr=True,
            )
            return

        # 统一过滤：lines/grep/column 复用 apply_lines_grep（原内联第三份复制已去除）
        output = apply_lines_grep(
            output, lines_param, grep, conn, column_param=req.column
        )
        if output is None:
            return
        assemble_response(
            ctx,
            conn,
            session,
            msg,
            output=output,
            matched=False,
            reason="ok" if session.running else "ended",
            result_type="read",
            has_trigger=False,
            consume_events=False,
            output_offset=cur_offset,
            include_debug=_include_debug(session),
            attach_stderr=True,
        )
