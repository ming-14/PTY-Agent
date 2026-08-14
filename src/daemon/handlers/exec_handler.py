import logging
import time
import traceback
from typing import Optional

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler, HandlerContext

_logger = logging.getLogger("pty-daemon")


class ExecHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        from ...config.common import (
            MAX_COMMAND_LEN,
            MAX_PATTERN_LEN,
            MAX_SESSION_ID_LEN,
        )
        from .utils import (
            GIT_BASH_PATH_HINT,
            apply_client_defaults,
            check_ended_session,
            has_git_bash_style_path,
            validate_request,
        )

        session_id = msg.get("id", "")
        command = msg.get("command")
        trigger = msg.get("trigger")
        if not validate_request(
            conn,
            msg,
            [
                ("id", MAX_SESSION_ID_LEN),
                ("command", MAX_COMMAND_LEN),
                ("trigger", MAX_PATTERN_LEN),
            ],
        ):
            return
        _logger.info(
            "_handle_exec: id=%r cmd=%r trigger=%r encoding=%r timeout=%r "
            "idle_timeout=%r idle_after_first=%r",
            session_id,
            command[:200] if isinstance(command, str) else command,
            trigger,
            msg.get("encoding"),
            msg.get("timeout"),
            msg.get("idle_timeout"),
            msg.get("idle_after_first_output"),
        )

        if not session_id:
            Message.send(conn, Response.error("Missing session id"))
            return
        if not command:
            Message.send(conn, Response.error("Missing command parameter"))
            return

        existing = ctx.manager.get_session(session_id)
        if existing:
            if not existing.running:
                Message.send(
                    conn,
                    Response.error(
                        f"Session '{session_id}' ended, kill and re-exec to restart"
                    ),
                )
                return
            session = existing
            _logger.info("会话 '%s' 已存在，直接附加", session_id)
        elif check_ended_session(ctx.manager, session_id):
            Message.send(
                conn,
                Response.error(
                    f"Session '{session_id}' ended, kill and re-exec to restart"
                ),
            )
            return
        else:
            # 插件解析：显式 --plugin 指定 + 自动加载（auto_load 条件命中）
            # 自动注入仅在会话创建时生效，已有会话附加不重复注入
            plugins = msg.get("plugins") or []
            auto = ctx.manager.match_auto_load(command, msg.get("cwd"), msg.get("env"))
            if auto:
                _logger.info("自动注入插件命中 (sid=%r): %s", session_id, auto)
            merged = auto + [p for p in plugins if p not in auto]
            try:
                session = ctx.manager.create_session(
                    session_id,
                    command,
                    encoding=msg.get("encoding"),
                    cwd=msg.get("cwd"),
                    env=msg.get("env"),
                    snapshot_mode=msg.get("snapshot_mode", False),
                    cols=msg.get("cols"),
                    rows=msg.get("rows"),
                    plugins=merged or None,
                )
                log_cmd = command if isinstance(command, str) else " ".join(command)
                _logger.info("创建会话 '%s': %s", session_id, log_cmd)
            except KeyError:
                Message.send(
                    conn, Response.error(f"Session '{session_id}' already exists")
                )
                return
            except ValueError as e:
                Message.send(conn, Response.error(str(e)))
                return
            except Exception as e:
                tb = traceback.format_exc()
                _logger.error("会话 '%s' 启动失败: %s", session_id, e)
                _logger.error(tb)
                err_msg = f"Failed to start session: {e}"
                if has_git_bash_style_path(command):
                    err_msg += f". {GIT_BASH_PATH_HINT}"
                Message.send(conn, Response.error(err_msg))
                return

        apply_client_defaults(session, msg)

        if session.snapshot_mode or msg.get("snapshot_mode"):
            _run_snapshot_flow(ctx, conn, session, msg, result_type="exec")
        elif trigger:
            trigger_offset = (
                0 if (msg.get("full") or not existing) else session.output_offset
            )
            start_offset = 0 if not existing else None
            _run_trigger_flow(
                ctx,
                conn,
                session,
                msg,
                trigger_offset,
                trigger,
                msg.get("newline", False),
                msg.get("fresh", False),
                msg.get("timeout", 120),
                start_offset=start_offset,
                result_type="exec",
            )
        else:
            _run_no_trigger_flow(ctx, conn, session, msg, result_type="exec")


def _run_snapshot_flow(
    ctx,
    conn,
    session,
    msg: dict,
    result_type: str = "exec",
    extra_fields: Optional[dict] = None,
    send_response: bool = True,
):
    from .utils import (
        attach_screen_buffer,
        build_result,
    )

    timeout = msg.get("timeout", 120)
    trigger = msg.get("trigger")
    idle_timeout = msg.get("idle_timeout")
    idle_after_first = msg.get("idle_after_first_output", False)
    keep_ansi = msg.get("keep_ansi", False)

    has_trigger = trigger is not None
    has_idle = idle_timeout is not None

    prior_snapshot = None
    if result_type != "exec" and (has_trigger or has_idle):
        prior_snapshot = session.get_snapshot(keep_ansi=keep_ansi)

    if has_trigger or has_idle:
        session.set_snapshot_trigger(
            pattern=trigger,
            idle_timeout=idle_timeout,
            idle_after_first_output=idle_after_first,
        )
        last_snapshot = ""
        deadline = time.time() + timeout
        if result_type == "exec":
            session.wait_for_initial_output(timeout=min(timeout, 2.0))

        host = getattr(session, "plugin_host", None)
        if host is not None:
            host.enter_wait()
        try:
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    matched, reason = False, "timeout"
                    break

                if host is not None:
                    plugin_reason = host.consume_return_request()
                    if plugin_reason:
                        matched, reason = True, plugin_reason
                        _logger.info(
                            "snapshot flow: PLUGIN_RETURN id=%r reason=%r",
                            session.id,
                            plugin_reason,
                        )
                        break

                snapshot = session.get_snapshot(keep_ansi=keep_ansi)

                if has_trigger:
                    if prior_snapshot is not None and snapshot == prior_snapshot:
                        pass
                    else:
                        check_text = snapshot
                        if prior_snapshot is not None:
                            prior_lines = prior_snapshot.splitlines()
                            cur_lines = snapshot.splitlines()
                            diff_lines = [
                                l
                                for i, l in enumerate(cur_lines)
                                if i >= len(prior_lines) or l != prior_lines[i]
                            ]
                            check_text = "\n".join(diff_lines)
                        if check_text and session.check_snapshot_trigger(check_text):
                            matched, reason = True, "matched"
                            break

                if has_idle:
                    if snapshot != last_snapshot:
                        last_snapshot = snapshot
                        session.notify_snapshot_changed()

                if has_idle and session.check_snapshot_idle_timeout():
                    matched, reason = False, "idle_timeout"
                    break

                if not session.running:
                    if session.process_monitor.crash_event.is_set():
                        matched, reason = False, "crashed"
                    else:
                        matched, reason = False, "ended"
                    break

                time.sleep(min(0.1, remaining))
        finally:
            if host is not None:
                host.exit_wait()

        session.clear_trigger()
    else:
        matched, reason = False, "ok"
        if result_type == "exec":
            session.wait_for_initial_output(timeout=min(timeout, 2.0))
        time.sleep(min(timeout, 1.0))

    if msg.get("snapshot_diff"):
        output = session.get_snapshot_diff(keep_ansi=keep_ansi)
    else:
        output = session.get_snapshot(keep_ansi=keep_ansi)
    result = build_result(
        ctx.manager,
        session.id,
        output,
        matched,
        reason,
        consume_events=True,
        has_trigger=has_trigger or has_idle,
        result_type=result_type,
        session=session,
        t_start=msg.get("_t_start"),
    )
    if not output:
        result["snapshotDiagnostics"] = session.get_snapshot_diagnostics()
    attach_screen_buffer(result, session, msg)
    if extra_fields:
        result.update(extra_fields)
    if send_response:
        Message.send(conn, result)
    else:
        return result, output


def _run_trigger_flow(
    ctx,
    conn,
    session,
    msg: dict,
    trigger_offset: int,
    trigger: str,
    newline: bool,
    fresh: bool,
    timeout: float,
    start_offset=None,
    result_type: str = "exec",
    extra_fields: Optional[dict] = None,
    send_response: bool = True,
):
    from .utils import attach_screen_buffer, build_result, strip_if_needed

    idle_timeout = msg.get("idle_timeout")
    idle_after_first = msg.get("idle_after_first_output", False)
    snapshot = msg.get("snapshot", False)

    if result_type == "exec":
        session.wait_for_initial_output(timeout=min(timeout, 2.0))

    scan_fresh = fresh and (start_offset is not None)
    if start_offset == 0:
        scan_fresh = False
    session.set_trigger(
        trigger,
        newline=newline,
        fresh=scan_fresh,
        start_offset=start_offset,
        idle_timeout=idle_timeout,
        idle_after_first_output=idle_after_first,
    )
    matched, reason = session.wait_for_trigger(timeout, gui_short_circuit=False)
    if snapshot or session.snapshot_mode:
        output = session.get_snapshot(keep_ansi=msg.get("keep_ansi", False))
    else:
        output = session.get_output(
            from_offset=trigger_offset, encoding=msg.get("encoding")
        )
        output = strip_if_needed(output, msg)
    result = build_result(
        ctx.manager,
        session.id,
        output,
        matched,
        reason,
        consume_events=True,
        has_trigger=True,
        result_type=result_type,
        session=session,
        t_start=msg.get("_t_start"),
    )
    if (snapshot or session.snapshot_mode) and not output:
        result["snapshotDiagnostics"] = session.get_snapshot_diagnostics()
    attach_screen_buffer(result, session, msg)
    if extra_fields:
        result.update(extra_fields)
    if send_response:
        Message.send(conn, result)
        session.clear_trigger()
    else:
        session.clear_trigger()
        return result, output


def _run_no_trigger_flow(
    ctx,
    conn,
    session,
    msg: dict,
    result_type: str = "exec",
    extra_fields: Optional[dict] = None,
    send_response: bool = True,
):
    from .utils import attach_screen_buffer, build_result, strip_if_needed

    idle_timeout = msg.get("idle_timeout")
    idle_after_first = msg.get("idle_after_first_output", False)
    snapshot = msg.get("snapshot", False)
    explicit_timeout = msg.get("explicit_timeout", False)

    session.wait_for_initial_output(timeout=0.5)

    if idle_timeout is not None:
        session.set_trigger(
            pattern=r"(?!x)x",
            newline=False,
            fresh=True,
            start_offset=session.output_offset,
            idle_timeout=idle_timeout,
            idle_after_first_output=idle_after_first,
        )
        matched, reason = session.wait_for_trigger(timeout=msg.get("timeout", 120))
        session.clear_trigger()
    elif explicit_timeout:
        session.set_trigger(
            pattern=r"(?!x)x",
            newline=False,
            fresh=True,
            start_offset=session.output_offset,
        )
        matched, reason = session.wait_for_trigger(timeout=msg.get("timeout", 120))
        session.clear_trigger()
    else:
        matched, reason = False, "ok"
        time.sleep(min(msg.get("timeout", 120), 1.0))

    if snapshot or session.snapshot_mode:
        output = session.get_snapshot(keep_ansi=msg.get("keep_ansi", False))
    else:
        output = session.get_output(encoding=msg.get("encoding"))
        output = strip_if_needed(output, msg)
    result = build_result(
        ctx.manager,
        session.id,
        output,
        matched,
        reason,
        consume_events=True,
        has_trigger=False,
        result_type=result_type,
        session=session,
        t_start=msg.get("_t_start"),
    )
    if (snapshot or session.snapshot_mode) and not output:
        result["snapshotDiagnostics"] = session.get_snapshot_diagnostics()
    attach_screen_buffer(result, session, msg)
    if extra_fields:
        result.update(extra_fields)
    if send_response:
        Message.send(conn, result)
    else:
        return result, output
