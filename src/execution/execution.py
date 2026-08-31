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
from ..protocol.reasons import Reason
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
    apply_filter: bool = False,
):
    from .output_policy import (
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
        # 循环内不变，提升出等待循环（避免每轮重复拆分）
        # 快照行由 CSI 光标定位序列分隔而非换行符，统一走 filtering 的屏幕行拆分
        from .filtering import _snapshot_lines

        prior_lines = _snapshot_lines(prior_snapshot) if prior_snapshot else []

    # [缓解方案] --newline 回显行排除：本次 send 写入的输入文本按行拆分，
    # 回显行（prompt + 输入文本）以输入行结尾，从匹配候选中剔除，避免
    # trigger 提前命中输入回显而非程序输出；子进程模式无终端回显，不涉及。
    # 每个输入行只声明一个"回显行内容"（首个以该输入行结尾的新行），
    # 此后仅内容相同的行被排除（回显行跨帧/跨迭代恒在屏幕，内容级精确
    # 排除避免重复帧泄漏）；不同内容的输出行（如 Hello Rikka）正常匹配。
    # 局限：终端回显与程序输出在信息层面不可完全区分（输出行与回显行
    # 内容完全相同或先于回显到达时仍会误排），仅缓解回显提前命中问题，
    # 非根本方案。
    echo_exclude_lines = None
    if cond.newline:
        sent = msg.get("input")
        if sent:
            echo_exclude_lines = [ln.strip() for ln in sent.splitlines() if ln.strip()]
    echo_claimed = {}

    if has_trigger or has_idle:
        session.set_snapshot_trigger(
            pattern=trigger,
            idle_timeout=idle_timeout,
            idle_after_first_output=idle_after_first,
        )
        # 触发等待序列持会话级锁：同一会话同时仅一个等待者
        # （前台命令与后台 notify worker 互斥，防 _trig_mat 状态并发覆写）
        session._trig_lock.acquire()
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
        # --newline 换行语义（对齐流式 TriggerMatcher.check 的"换行后检查"）：
        # 快照换行数较上次检查增加（出现新的完整行）才允许匹配；set 后首次
        # 检查放行一次（覆盖 set 前已在输出中的行），与流式 _newline_first_ok 一致。
        snap_nl_count = 0
        first_nl_check = True
        try:
            # 迭代骨架（cancel/remaining/timeout/循环）复用统一等待引擎；
            # 检查顺序与 sleep 原语保持原样（行为零变化）
            from ..session.wait import wait_reason

            def _iteration(remaining):
                nonlocal last_key, last_rendered, last_snapshot, _last_gui_check
                nonlocal snap_nl_count, first_nl_check
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
                        newline_ok = True
                        if cond.newline:
                            cur_nl = snapshot.count("\n")
                            if cur_nl > snap_nl_count:
                                snap_nl_count = cur_nl
                            elif first_nl_check:
                                first_nl_check = False
                            else:
                                newline_ok = False
                        if newline_ok:
                            check_text = snapshot
                            if prior_lines:
                                from .filtering import _snapshot_lines

                                cur_lines = _snapshot_lines(snapshot)
                                diff_lines = [
                                    l
                                    for i, l in enumerate(cur_lines)
                                    if i >= len(prior_lines) or l != prior_lines[i]
                                ]
                                # 回显行剔除（缓解方案）：首个以输入行结尾的新行声明为回显，
                                # 此后仅内容相同的行被排除（防重复帧泄漏）
                                if echo_exclude_lines:
                                    kept = []
                                    for l in diff_lines:
                                        s = l.strip()
                                        echo_e = next(
                                            (e for e in echo_exclude_lines if s.endswith(e)),
                                            None,
                                        )
                                        if echo_e is not None:
                                            if echo_e not in echo_claimed:
                                                echo_claimed[echo_e] = s
                                                continue
                                            if echo_claimed[echo_e] == s:
                                                continue
                                        kept.append(l)
                                    diff_lines = kept
                                check_text = "\n".join(diff_lines)
                            if check_text and session.check_snapshot_trigger(check_text):
                                return True, Reason.MATCHED

                if has_idle:
                    if snapshot != last_snapshot:
                        last_snapshot = snapshot
                        session.notify_snapshot_changed()

                if has_idle and session.check_snapshot_idle_timeout():
                    return False, Reason.IDLE_TIMEOUT

                # GUI 窗口检测：检测到新窗口即短路返回（与子进程 wait_for_trigger 语义一致）
                detected, _last_gui_check = session.check_gui_detected(
                    _last_gui_check
                )
                if detected:
                    return False, Reason.GUI_DETECTED

                session.poll_natural_exit()
                if not session.running:
                    return False, session.resolve_exit_reason()

                time.sleep(min(0.1, remaining))
                return None

            matched, reason = wait_reason(
                deadline=deadline,
                cancel_event=cancel_event,
                iteration=_iteration,
                on_timeout=lambda: (False, Reason.TIMEOUT),
            )
        finally:
            if host is not None:
                host.exit_wait()
            session.clear_trigger()
            session._trig_lock.release()
    else:
        matched, reason = False, Reason.OK
        # 显式 --timeout：以完整 timeout 为等待预算等待返回条件（命令自然结束/
        # GUI/进程变化），到期以 reason=timeout 返回（会话保持运行，可继续 send/read）；
        # 无显式 timeout：send/read/no-trigger 沿用固定短等待（min(timeout,1.0)）拿当前快照
        if explicit_timeout:
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
                return False, Reason.GUI_DETECTED
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
                (lambda: (False, Reason.TIMEOUT))
                if explicit_timeout
                else (lambda: NO_RETURN)
            ),
        )
        if result is not NO_RETURN:
            matched, reason = result

    output = resolve_output(session, cond)
    # exec/send/mouse 行数过滤（-l）：read 由 read_handler 自行过滤（避免双重过滤）
    if apply_filter and req.lines is not None:
        from .filtering import filter_snapshot_lines

        output = filter_snapshot_lines(output, req.lines)
    return assemble_response(
        ctx,
        conn,
        session,
        msg,
        output=output,
        matched=matched,
        reason=reason,
        result_type=result_type,
        extra_fields=extra_fields,
        send_response=send_response,
        snapshot_diagnostics=True,
        include_debug=msg.get("debug"),
    )


def _interruptible_sleep(duration: float, cancel_event, on_cancel):
    """分段睡眠：cancel_event 置位时执行 on_cancel 回调并提前返回

    长等待（无 trigger 的固定等待）在取消时可及时中断，回调返回后
    上层即以 reason=cancelled 结束步骤。有 cancel_event 时一律分段
    睡眠并检查，避免短等待（≤1s）期间取消被延迟。
    """
    if cancel_event is None:
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
    consume_events: bool = True,
    extra_fields: Optional[dict] = None,
    send_response: bool = True,
    output_offset: Optional[int] = None,
    include_debug: Optional[bool] = None,
    snapshot_diagnostics: bool = False,
    attach_stderr: bool = False,
    warning: Optional[str] = None,
):
    """统一响应装配器：build_result → (diagnostics/stderr) → attach_screen → extra → send/return

    把 execution.py 三个流程与 read_handler / workflow 多处重复的"装配+发送"尾部
    收敛到一处；send_response=False 时返回 (result, output) 供 workflow 等调用方
    自行处理。纯结构归一，不改变任何既有装配顺序与字段语义。
    """
    from .response import (
        attach_screen_buffer,
        build_result,
        describe_output_format,
    )

    result = build_result(
        ctx.manager,
        session.id,
        output,
        matched,
        reason,
        consume_events=consume_events,
        result_type=result_type,
        session=session,
        t_start=msg.get("_t_start"),
        output_offset=output_offset,
        include_debug=include_debug,
        warning=warning,
    )
    if not output and snapshot_diagnostics:
        result["snapshotDiagnostics"] = session.get_snapshot_diagnostics()
    # 过滤/取源格式标签（供 presenter 分隔线显示）
    # 子进程模式为增量输出（无快照），标注 diff；pty 模式按过滤方式标注
    result["format"] = describe_output_format(
        msg, is_subprocess=getattr(session, "mode", "pty") == "subprocess"
    )
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
    apply_filter: bool = False,
):
    """子进程模式 trigger 流程：增量文本匹配（复用 TriggerMatcher）"""
    from .filtering import strip_if_needed
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
    # 触发等待序列持会话级锁：同一会话同时仅一个等待者（前台/后台互斥）
    session._trig_lock.acquire()
    try:
        matched, reason = session.wait_for_trigger(
            timeout, gui_short_circuit=False, cancel_event=cancel_event
        )
        output, delivered_end = session.get_output_with_offset(
            from_offset=trigger_offset, encoding=req.encoding
        )
        # 增量交付：推进消费游标到已交付末尾
        session.advance_stdout_cursor(delivered_end)
        output = strip_if_needed(output, msg)
        # exec/send 行数过滤（-l）：子进程模式行按换行分隔，直接 apply_lines_grep
        if apply_filter and req.lines is not None:
            from .filtering import apply_lines_grep

            filtered = apply_lines_grep(output, req.lines, None, conn)
            if filtered is None:
                return
            output = filtered
        ret = assemble_response(
            ctx,
            conn,
            session,
            msg,
            output=output,
            matched=matched,
            reason=reason,
            result_type=result_type,
            extra_fields=extra_fields,
            send_response=send_response,
            attach_stderr=True,
            include_debug=msg.get("debug"),
        )
    finally:
        session.clear_trigger()
        session._trig_lock.release()
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
    apply_filter: bool = False,
):
    """子进程模式无 trigger 流程：idle-timeout / 固定等待 → 增量输出 + stderr

    from_offset=None 时按 msg 计算：--full 从 0 读，否则从当前 offset 增量读
    （对齐 trigger 流程的增量语义，避免重复 exec/send 返回全量副本）。
    """
    from .filtering import strip_if_needed
    from .conditions import RequestContext

    req = RequestContext.from_msg(msg)
    cond = req.cond
    idle_timeout = cond.idle_timeout
    idle_after_first = cond.idle_after_first
    explicit_timeout = cond.explicit_timeout

    # 增量基准：必须在等待前捕获，才能返回等待期间的新增输出。
    # 默认用 stdout 消费游标（而非写入末尾），避免两次调用之间写入的输出被跳过。
    if from_offset is None:
        from_offset = session.read_base(cond.full)

    session.wait_for_initial_output(timeout=0.5)

    if idle_timeout is not None:
        session._trig_lock.acquire()
        try:
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
        finally:
            session.clear_trigger()
            session._trig_lock.release()
    elif explicit_timeout:
        session._trig_lock.acquire()
        try:
            session.set_trigger(
                pattern=r"(?!x)x",
                newline=False,
                fresh=True,
                start_offset=session.output_offset,
            )
            matched, reason = session.wait_for_trigger(
                timeout=cond.timeout, cancel_event=cancel_event
            )
        finally:
            session.clear_trigger()
            session._trig_lock.release()
    else:
        matched, reason = False, Reason.OK

        def _on_cancel():
            nonlocal matched, reason
            matched, reason = False, Reason.CANCELLED

        _interruptible_sleep(min(cond.timeout, 1.0), cancel_event, _on_cancel)

    output, delivered_end = session.get_output_with_offset(
        from_offset=from_offset, encoding=req.encoding
    )
    # 增量交付：推进消费游标到已交付末尾（后续默认读取不再重复此段）
    session.advance_stdout_cursor(delivered_end)
    output = strip_if_needed(output, msg)
    # exec/send 行数过滤（-l）
    if apply_filter and req.lines is not None:
        from .filtering import apply_lines_grep

        filtered = apply_lines_grep(output, req.lines, None, conn)
        if filtered is None:
            return
        output = filtered
    return assemble_response(
        ctx,
        conn,
        session,
        msg,
        output=output,
        matched=matched,
        reason=reason,
        result_type=result_type,
        extra_fields=extra_fields,
        send_response=send_response,
        attach_stderr=True,
        include_debug=msg.get("debug"),
    )