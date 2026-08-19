"""会话执行流程 — exec/send/read 的核心动作

从 handlers 抽离的执行原语：快照流程 / 子进程触发流程 / 子进程无触发流程。
exec/send/read handler 与 workflow 引擎共用同一执行核心，避免行为分叉。

所有函数支持 send_response=False 时返回 (result, output) 供调用方自行处理；
cancel_event（threading.Event）置位时等待循环以 reason=cancelled 提前返回，
供 workflow 等后台调用方中断长时间等待。
"""

import threading
import time
from typing import Optional

from ..protocol.message import Message
from ..logging import get_logger

_logger = get_logger("pty-daemon")


def _run_snapshot_flow(
    ctx,
    conn,
    session,
    msg: dict,
    result_type: str = "exec",
    extra_fields: Optional[dict] = None,
    send_response: bool = True,
    cancel_event: Optional[threading.Event] = None,
):
    from .handlers.utils import (
        resolve_output,
    )
    from .conditions import RequestContext

    req = RequestContext.from_msg(msg)
    cond = req.cond
    timeout = cond.timeout
    trigger = cond.trigger
    idle_timeout = cond.idle_timeout
    idle_after_first = cond.idle_after_first
    keep_ansi = cond.keep_ansi
    explicit_timeout = cond.explicit_timeout

    has_trigger = trigger is not None
    has_idle = idle_timeout is not None

    prior_snapshot = None
    prior_lines = []
    if result_type != "exec" and (has_trigger or has_idle):
        prior_snapshot = session.get_snapshot(keep_ansi=keep_ansi)
        # 循环内不变，提升出等待循环（避免每轮重复 splitlines）
        prior_lines = prior_snapshot.splitlines() if prior_snapshot else []

    if has_trigger or has_idle:
        session.set_snapshot_trigger(
            pattern=trigger,
            idle_timeout=idle_timeout,
            idle_after_first_output=idle_after_first,
            newline=cond.newline,
        )
        last_snapshot = ""
        deadline = time.time() + timeout
        if result_type == "exec":
            session.wait_for_initial_output(timeout=min(timeout, 2.0))

        host = getattr(session, "plugin_host", None)
        if host is not None:
            host.enter_wait()
        # 快照内容仅随 feed/resize 变化（feed_count/cols/rows 不变量，
        # 与 get_snapshot_diff 同源）：未变化时跳过全量渲染 + 行对比
        screen = getattr(session, "_screen", None)
        last_key = None
        last_rendered = ""
        _last_gui_check = 0.0
        try:
            # 迭代骨架（cancel/remaining/timeout/循环）复用统一等待引擎；
            # 检查顺序与 sleep 原语保持原样（行为零变化）
            from ..session.wait import wait_reason

            def _iteration(remaining):
                nonlocal last_key, last_rendered, last_snapshot, _last_gui_check
                if host is not None:
                    plugin_reason = host.consume_return_request()
                    if plugin_reason:
                        _logger.info(
                            "snapshot flow: PLUGIN_RETURN id=%r reason=%r",
                            session.id,
                            plugin_reason,
                        )
                        return True, plugin_reason

                if screen is not None:
                    key = (screen.feed_count, screen.cols, screen.rows, keep_ansi)
                else:
                    # 无终端模型（子进程/mock）：无法确认内容变化源，保持每次渲染
                    key = object()
                if key != last_key:
                    last_key = key
                    snapshot = session.get_snapshot(keep_ansi=keep_ansi)
                    last_rendered = snapshot
                else:
                    # 无变化：内容与上一轮渲染一致，直接复用
                    snapshot = last_rendered

                if has_trigger:
                    if prior_snapshot is None or snapshot != prior_snapshot:
                        check_text = snapshot
                        if prior_lines:
                            cur_lines = snapshot.splitlines()
                            diff_lines = [
                                l
                                for i, l in enumerate(cur_lines)
                                if i >= len(prior_lines) or l != prior_lines[i]
                            ]
                            check_text = "\n".join(diff_lines)
                        if check_text and session.check_snapshot_trigger(check_text):
                            return True, "matched"

                if has_idle:
                    if snapshot != last_snapshot:
                        last_snapshot = snapshot
                        session.notify_snapshot_changed()

                if has_idle and session.check_snapshot_idle_timeout():
                    return False, "idle_timeout"

                # GUI 窗口检测：检测到新窗口即短路返回（与子进程 wait_for_trigger 语义一致）
                detected, _last_gui_check = session.check_gui_detected(
                    _last_gui_check
                )
                if detected:
                    return False, "gui_detected"

                session.poll_natural_exit()
                if not session.running:
                    return False, session.resolve_exit_reason()

                time.sleep(min(0.1, remaining))
                return None

            matched, reason = wait_reason(
                deadline=deadline,
                cancel_event=cancel_event,
                iteration=_iteration,
                on_timeout=lambda: (False, "timeout"),
            )
        finally:
            if host is not None:
                host.exit_wait()

        session.clear_trigger()
    else:
        matched, reason = False, "ok"
        # exec 显式 --timeout：以完整 timeout 为等待预算等待命令自然结束，
        # 到期以 reason=timeout 返回（会话保持运行，供后续 send/read 继续使用）；
        # 其余（read 轮询 / exec 非显式）沿用固定短等待分片复查
        if result_type == "exec" and explicit_timeout:
            deadline = time.time() + timeout
        else:
            if result_type == "exec":
                session.wait_for_initial_output(timeout=min(timeout, 2.0))
            deadline = time.time() + min(timeout, 1.0)
        # 分片复查：程序可能在等待期间结束/崩溃，应如实上报而非恒 ok；
        # 每片同步推进自然结束检测（监控线程按 2s 低频 tick 触发，仅等它
        # 扫到前会误判程序持续运行直至超时）
        # 迭代骨架复用统一等待引擎：GUI/结束检查 + sleep 顺序保持原样
        from ..session.wait import NO_RETURN, wait_reason

        _last_gui_check = 0.0

        def _iteration(remaining):
            nonlocal _last_gui_check
            # GUI 窗口检测：与 trigger 等待分支同一套语义（节流 1s 主动轮询，
            # 检测到新窗口即短路返回 —— 实现「GUI 检测在无 trigger 等待时生效」）
            detected, _last_gui_check = session.check_gui_detected(
                _last_gui_check
            )
            if detected:
                return False, "gui_detected"
            if not session.running:
                return False, session.resolve_exit_reason()
            session.poll_natural_exit()
            time.sleep(min(0.1, remaining))
            return None

        result = wait_reason(
            deadline=deadline,
            cancel_event=cancel_event,
            iteration=_iteration,
            on_timeout=(
                (lambda: (False, "timeout"))
                if result_type == "exec" and explicit_timeout
                else (lambda: NO_RETURN)
            ),
        )
        if result is not NO_RETURN:
            matched, reason = result

    output = resolve_output(session, cond)
    return assemble_response(
        ctx,
        conn,
        session,
        msg,
        output=output,
        matched=matched,
        reason=reason,
        result_type=result_type,
        has_trigger=has_trigger or has_idle,
        extra_fields=extra_fields,
        send_response=send_response,
        snapshot_diagnostics=True,
    )


def _interruptible_sleep(duration: float, cancel_event, on_cancel):
    """分段睡眠：cancel_event 置位时执行 on_cancel 回调并提前返回

    长等待（无 trigger 的固定等待）在取消时可及时中断，回调返回后
    上层即以 reason=cancelled 结束步骤。
    """
    if cancel_event is None or duration <= 1.0:
        time.sleep(duration)
        return
    deadline = time.time() + duration
    while time.time() < deadline:
        time.sleep(min(0.1, deadline - time.time()))
        if cancel_event.is_set():
            if on_cancel is not None:
                on_cancel()
            return


def _attach_subprocess_stderr(result: dict, session, msg: dict) -> None:
    """子进程模式：附加 stderr 到结果（stderrOutput 字段）

    增量读取：每次只返回自上次以来的新增 stderr，并推进 stderrOutputOffset
    （与 stdout 增量语义一致，避免跨命令重复返回同一段 stderr）。
    """
    if getattr(session, "mode", "pty") != "subprocess":
        return
    err_output = session.read_new_err_output(encoding=msg.get("encoding"))
    if err_output:
        result["stderrOutput"] = err_output
    result["stderrOutputOffset"] = session.stderr_read_offset


def assemble_response(
    ctx,
    conn,
    session,
    msg: dict,
    *,
    output: str,
    matched: bool,
    reason: str,
    result_type: str,
    has_trigger: bool,
    consume_events: bool = True,
    extra_fields: Optional[dict] = None,
    send_response: bool = True,
    output_offset: Optional[int] = None,
    include_debug: Optional[bool] = None,
    snapshot_diagnostics: bool = False,
    attach_stderr: bool = False,
    warning: Optional[str] = None,
):
    """统一响应装配器（P0-A 步2）：build_result → (diagnostics/stderr) → attach_screen → extra → send/return

    把 execution.py 三个流程与 read_handler / workflow 多处重复的"装配+发送"尾部
    收敛到一处；send_response=False 时返回 (result, output) 供 workflow 等调用方
    自行处理。纯结构归一，不改变任何既有装配顺序与字段语义。
    """
    from .handlers.utils import (
        attach_screen_buffer,
        build_result,
    )

    result = build_result(
        ctx.manager,
        session.id,
        output,
        matched,
        reason,
        consume_events=consume_events,
        has_trigger=has_trigger,
        result_type=result_type,
        session=session,
        t_start=msg.get("_t_start"),
        output_offset=output_offset,
        include_debug=include_debug,
        warning=warning,
    )
    if not output and snapshot_diagnostics:
        result["snapshotDiagnostics"] = session.get_snapshot_diagnostics()
    if attach_stderr:
        _attach_subprocess_stderr(result, session, msg)
    attach_screen_buffer(result, session, msg)
    if extra_fields:
        result.update(extra_fields)
    if send_response:
        Message.send(conn, result)
        return None
    return result, output


def _run_subprocess_trigger_flow(
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
    cancel_event: Optional[threading.Event] = None,
):
    """子进程模式 trigger 流程：增量文本匹配（复用 TriggerMatcher）"""
    from .handlers.utils import strip_if_needed
    from .conditions import RequestContext

    req = RequestContext.from_msg(msg)
    cond = req.cond
    idle_timeout = cond.idle_timeout
    idle_after_first = cond.idle_after_first
    explicit_timeout = cond.explicit_timeout

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
    matched, reason = session.wait_for_trigger(
        timeout, gui_short_circuit=False, cancel_event=cancel_event
    )
    output = session.get_output(
        from_offset=trigger_offset, encoding=req.encoding
    )
    output = strip_if_needed(output, msg)
    ret = assemble_response(
        ctx,
        conn,
        session,
        msg,
        output=output,
        matched=matched,
        reason=reason,
        result_type=result_type,
        has_trigger=True,
        extra_fields=extra_fields,
        send_response=send_response,
        attach_stderr=True,
    )
    session.clear_trigger()
    return ret


def _run_subprocess_no_trigger_flow(
    ctx,
    conn,
    session,
    msg: dict,
    result_type: str = "exec",
    extra_fields: Optional[dict] = None,
    send_response: bool = True,
    from_offset: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
):
    """子进程模式无 trigger 流程：idle-timeout / 固定等待 → 增量输出 + stderr

    from_offset=None 时按 msg 计算：--full 从 0 读，否则从当前 offset 增量读
    （对齐 trigger 流程的增量语义，避免重复 exec/send 返回全量副本）。
    """
    from .handlers.utils import strip_if_needed
    from .conditions import RequestContext

    req = RequestContext.from_msg(msg)
    cond = req.cond
    idle_timeout = cond.idle_timeout
    idle_after_first = cond.idle_after_first
    explicit_timeout = cond.explicit_timeout

    # 增量基准：必须在等待前捕获，才能返回等待期间的新增输出
    if from_offset is None:
        from_offset = 0 if cond.full else session.output_offset

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
        matched, reason = session.wait_for_trigger(
            timeout=cond.timeout, cancel_event=cancel_event
        )
        session.clear_trigger()
    elif explicit_timeout:
        session.set_trigger(
            pattern=r"(?!x)x",
            newline=False,
            fresh=True,
            start_offset=session.output_offset,
        )
        matched, reason = session.wait_for_trigger(
            timeout=cond.timeout, cancel_event=cancel_event
        )
        session.clear_trigger()
    else:
        matched, reason = False, "ok"

        def _on_cancel():
            nonlocal matched, reason
            matched, reason = False, "cancelled"

        _interruptible_sleep(min(cond.timeout, 1.0), cancel_event, _on_cancel)

    output = session.get_output(from_offset=from_offset, encoding=req.encoding)
    output = strip_if_needed(output, msg)
    return assemble_response(
        ctx,
        conn,
        session,
        msg,
        output=output,
        matched=matched,
        reason=reason,
        result_type=result_type,
        has_trigger=False,
        extra_fields=extra_fields,
        send_response=send_response,
        attach_stderr=True,
    )