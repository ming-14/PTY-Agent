import re
import time

from ...session.trigger_matcher import safe_regex_search
from ...protocol.message import Message
from ...protocol.reasons import Reason
from ...protocol.response import Response
from ...execution import (
    _run_snapshot_flow,
    _run_subprocess_no_trigger_flow,
    _run_subprocess_trigger_flow,
    assemble_response,
)
from .base import DaemonHandler
from ...execution.context import HandlerContext
from ...execution.filtering import (
    apply_lines_grep,
    filter_snapshot_lines,
    strip_if_needed,
)
from ...execution.output_policy import resolve_output, validate_offset_policy
from ...execution.response import (
    attach_screen_buffer,
    describe_output_format,
    format_iso_ms,
)
from ...execution.utils import (
    apply_client_defaults,
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
                    trigger_reason = Reason.PROGRAM_ENDED
                    if trigger and output:
                        try:
                            if safe_regex_search(re.compile(trigger), output):
                                trigger_reason = Reason.TRIGGER_MATCHED
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
                            "mode": "subprocess" if pty_type == "subprocess" else "pty",
                        },
                        # 会话结束提示由呈现层按 running/reason 数据重建（见 presenter._session_reason_hint）
                        "format": describe_output_format(
                            msg,
                            is_subprocess=pty_type == "subprocess",
                        ),
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
        from ...execution.conditions import RequestContext

        if not apply_client_defaults(
            session, msg, conn, global_defaults=ctx.manager.get_global_defaults()
        ):
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
        # 与子进程模式对齐"都不带 → 1s 后返回"语义：无参数快照先等 1s 收集
        # 输出再返回（--default timeout 可调整等待时长）；查询类（--full/-l/
        # --column/--offset）立即返回不等待
        # 显式 --offset：与子进程模式语义一致，从原始输出缓冲区增量读取，
        # 返回"该偏移之后的新增输出"，响应 outputOffset 为缓冲结束偏移。
        # （offset 未显式给定时保持快照语义，read 默认返回可见屏幕快照）
        is_query = cond.full or lines_param is not None or req.column is not None
        if offset is None and not is_query:
            time.sleep(min(cond.timeout, 1.0))
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
                reason=Reason.OK if session.running else Reason.ENDED,
                result_type="read",
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
            reason=Reason.OK if session.running else Reason.ENDED,
            result_type="read",
            consume_events=False,
            include_debug=_include_debug(session),
            snapshot_diagnostics=True,
        )

    def _handle_subprocess_read(self, ctx, conn, session, msg: dict):
        """子进程模式 read：增量交付 / 累积查询 + stderr

        读取策略（L2，见 Session.read_base/advance_stdout_cursor）：
        - 默认（无返回条件、无查询参数）：对齐 exec/send，等待 1s 后增量交付，推进消费游标。
        - --offset N：从指定绝对流偏移增量交付，推进消费游标。
        - --full / -l / --column：累积输出查询，从保留起点读取，**不**推进消费游标。
        - --trigger/--idle-timeout/--timeout：等待后增量交付（复用 execution 流程）。
        - --grep：子进程模式不支持（仅终端模式可用），拒绝。
        """
        from ...execution.conditions import RequestContext
        from ...execution.filtering import strip_if_needed

        req = RequestContext.from_msg(msg)
        cond = req.cond
        lines_param = req.lines
        grep = req.grep
        offset = req.offset
        encoding = req.encoding
        has_wait = cond.has_wait

        # --grep 仅终端模式可用（grep 基于终端可见屏幕语义；子进程无快照）
        if grep is not None:
            Message.send(
                conn,
                Response.error("子进程模式不支持 --grep（grep 仅终端模式可用）"),
            )
            return

        if not validate_offset_policy(
            conn,
            offset,
            lines=lines_param,
            full=cond.full,
            waiting=has_wait,
        ):
            return

        if has_wait:
            # 等待路径：增量交付（trigger/idle/timeout），消费游标由 execution 流程推进
            if cond.trigger:
                result, output = _run_subprocess_trigger_flow(
                    ctx,
                    conn,
                    session,
                    msg,
                    session.read_base(cond.full),
                    cond.trigger,
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
                )
            # 等待返回的是本次交付增量；-l/--column 应用于该增量
            if lines_param is not None or req.column is not None:
                output = apply_lines_grep(
                    output, lines_param, None, conn, column_param=req.column
                )
                if output is None:
                    return
            result["outputStream"] = output
            attach_screen_buffer(result, session, msg)
            Message.send(conn, result)
            return

        # 非等待：区分"增量消费"与"累积查询"
        if offset is not None:
            # 显式 --offset：从指定绝对流偏移增量交付，推进消费游标
            output, delivered_end = session.get_output_with_offset(
                from_offset=offset, encoding=encoding
            )
            output = strip_if_needed(output, msg)
            session.advance_stdout_cursor(delivered_end)
        else:
            is_query = cond.full or lines_param is not None or req.column is not None
            if is_query:
                # 累积输出查询：从保留起点读取，不推进消费游标（--full 倾倒 / -l/--column 取行）
                base = 0
            else:
                # 无参数：对齐 exec/send 的 1s 兜底等待后增量交付（见返回条件表"都不带→1s后返回"）
                time.sleep(min(cond.timeout, 1.0))
                base = session.read_base(cond.full)
            output, delivered_end = session.get_output_with_offset(
                from_offset=base, encoding=encoding
            )
            output = strip_if_needed(output, msg)
            if not is_query:
                session.advance_stdout_cursor(delivered_end)
            # 累积查询仅在参数齐全时应用行/列过滤
            if lines_param is not None or req.column is not None:
                filtered = apply_lines_grep(
                    output, lines_param, None, conn, column_param=req.column
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
            reason=Reason.OK if session.running else Reason.ENDED,
            result_type="read",
            consume_events=False,
            output_offset=delivered_end,
            include_debug=_include_debug(session),
            attach_stderr=True,
        )
