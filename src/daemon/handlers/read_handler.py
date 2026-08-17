import re
import time

from ...output import safe_regex_search
from ...protocol.message import Message
from ...protocol.response import Response
from ..execution import (
    _attach_subprocess_stderr,
    _run_snapshot_flow,
    _run_subprocess_no_trigger_flow,
    _run_subprocess_trigger_flow,
)
from .base import DaemonHandler, HandlerContext
from .utils import (
    _SESSION_ENDED_HINT,
    apply_client_defaults,
    apply_lines_grep,
    attach_screen_buffer,
    build_result,
    filter_snapshot_lines,
    format_iso_ms,
    strip_if_needed,
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

        if offset is not None and has_wait:
            Message.send(
                conn,
                Response.error(
                    "--offset cannot be used with --trigger/--idle-timeout/--timeout (waiting mode)"
                ),
            )
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
        apply_client_defaults(session, msg)

        session_id = session.id
        lines_param = msg.get("lines")
        grep = msg.get("grep")
        offset = msg.get("offset")
        idle_timeout = msg.get("idle_timeout")
        explicit_timeout = msg.get("explicit_timeout", False)
        trigger = msg.get("trigger")
        has_wait = trigger is not None or idle_timeout is not None or explicit_timeout

        is_sub = getattr(session, "mode", "pty") == "subprocess"
        if is_sub:
            if msg.get("snapshot_diff"):
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
        if offset is not None and lines_param is not None:
            Message.send(
                conn, Response.error("--offset cannot be used with --lines/-l")
            )
            return
        if offset is not None and msg.get("full"):
            Message.send(conn, Response.error("--offset cannot be used with --full"))
            return

        if has_wait:
            # --lines 需作用于含 scrollback 历史的全量内容（隐式 full）
            wait_msg = msg
            if (
                lines_param is not None
                and not msg.get("full")
                and not msg.get("snapshot_diff")
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
                output, lines_param, msg.get("column"), grep
            )
            # 过滤结果必须写回响应：build_result 装配的是未过滤快照，
            # 此前漏写回导致等待模式下 -g/-l/--column 全部静默失效
            result["outputStream"] = output
            attach_screen_buffer(result, session, msg)
            Message.send(conn, result)
            return

        # 非等待：直接返回快照（--full/--lines 时含 scrollback 历史）
        if msg.get("snapshot_diff"):
            output = session.get_snapshot_diff(keep_ansi=msg.get("keep_ansi", False))
        elif msg.get("full") or lines_param is not None:
            output = session.get_full_snapshot(keep_ansi=msg.get("keep_ansi", False))
        else:
            output = session.get_snapshot(keep_ansi=msg.get("keep_ansi", False))
        output = filter_snapshot_lines(output, lines_param, msg.get("column"), grep)
        result = build_result(
            ctx.manager,
            session_id,
            output,
            False,
            "ok" if session.running else "ended",
            has_trigger=False,
            result_type="read",
            session=session,
            include_debug=_include_debug(session),
        )
        if not output:
            result["snapshotDiagnostics"] = session.get_snapshot_diagnostics()
        attach_screen_buffer(result, session, msg)
        Message.send(conn, result)

    def _handle_subprocess_read(self, ctx, conn, session, msg: dict):
        """子进程模式 read：增量读取 stdout + 附加 stderr

        支持 --offset / --full / -l / -g / --timeout / --idle-timeout / --trigger。
        """
        from ..execution import _attach_subprocess_stderr
        from .utils import attach_screen_buffer, build_result, strip_if_needed

        lines_param = msg.get("lines")
        grep = msg.get("grep")
        offset = msg.get("offset")
        encoding = msg.get("encoding")
        trigger = msg.get("trigger")
        idle_timeout = msg.get("idle_timeout")
        explicit_timeout = msg.get("explicit_timeout", False)
        has_wait = trigger is not None or idle_timeout is not None or explicit_timeout

        if offset is not None and has_wait:
            Message.send(
                conn,
                Response.error(
                    "--offset cannot be used with --trigger/--idle-timeout/--timeout (waiting mode)"
                ),
            )
            return

        if has_wait:
            if trigger:
                trigger_offset = 0 if msg.get("full") else session.output_offset
                result, output = _run_subprocess_trigger_flow(
                    ctx,
                    conn,
                    session,
                    msg,
                    trigger_offset,
                    trigger,
                    msg.get("newline", False),
                    msg.get("fresh", True),
                    msg.get("timeout", 120),
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
                        0 if msg.get("full") else session.output_offset
                    ),
                )
            output = apply_lines_grep(
                output, lines_param, grep, conn,
                column_param=msg.get("column"),
            )
            if output is None:
                return
            result["outputStream"] = output
            attach_screen_buffer(result, session, msg)
            Message.send(conn, result)
            return

        # 非等待：增量读取 stdout
        if msg.get("full"):
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
            and msg.get("column") is None
        ):
            sid = session.id
            result = build_result(
                ctx.manager,
                sid,
                output,
                False,
                "ok" if session.running else "ended",
                has_trigger=False,
                result_type="read",
                session=session,
                t_start=msg.get("_t_start"),
                output_offset=cur_offset,
                include_debug=_include_debug(session),
            )
            _attach_subprocess_stderr(result, session, msg)
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
                    start = max(start, 1)
                    lines = lines[start - 1 : end]
                except (ValueError, IndexError):
                    Message.send(
                        conn, Response.error(f"Invalid line range: {lines_param}")
                    )
                    return
            else:
                try:
                    n = int(lines_param)
                    lines = lines[-n:] if n > 0 else []
                except ValueError:
                    Message.send(
                        conn, Response.error(f"Invalid lines parameter: {lines_param}")
                    )
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
                line[col_idx] if 0 <= col_idx < len(line) else "" for line in lines
            ]

        output = "\n".join(lines)
        sid = session.id
        result = build_result(
            ctx.manager,
            sid,
            output,
            False,
            "ok" if session.running else "ended",
            has_trigger=False,
            result_type="read",
            session=session,
            t_start=msg.get("_t_start"),
            output_offset=cur_offset,
            include_debug=_include_debug(session),
        )
        _attach_subprocess_stderr(result, session, msg)
        attach_screen_buffer(result, session, msg)
        Message.send(conn, result)
