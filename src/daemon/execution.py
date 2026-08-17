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


def _kill_exec_session_on_timeout(ctx, session) -> None:
    """exec 显式 --timeout 到期：终止会话进程树并触发管理器移除

    仅 exec 发起命令时的显式超时到期调用（read/send 等轮询等待不适用）。

    - stop() 幂等（防重入）：强杀进程树 + 关闭 PTY + 停止后台线程；
    - 读者线程被 EOF/stop_event 唤醒退出，走既有自然结束链
      （on_exit → notify_end → manager._on_session_ended）从活跃表移除
      并归档；此处仅在会话仍处于活跃表时兜底触发移除（幂等），
      读者线程已触发则跳过，避免重复广播 end 事件；
    - 响应构造所需的 exit_code/error_message/processes 等元数据在
      stop 后仍可用：release_components 仅在被移除且无 handler 持有
      时执行，flow 内持有期间释放一律转 pending。
    """
    _logger.info("exec 显式超时到期，终止会话 '%s' 进程树", session.id)
    try:
        session.stop()
    except Exception as e:
        _logger.warning("终止会话 '%s' 进程树异常: %s", session.id, e)
    # 兜底触发管理器移除：读者线程在 stop join 期间已触发则跳过
    try:
        manager = getattr(ctx, "manager", None) if ctx is not None else None
        if manager is not None and manager.get_session(session.id) is session:
            session.publisher.notify_end(session)
    except Exception:
        pass
    # 兜底登记组件释放：读者线程在 stop join 期间触发的 release_components
    # 可能因 _stop_finished 尚未置位而早退，此处补登（持锁期间转 pending）
    try:
        session.release_components()
    except Exception:
        pass


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
        attach_screen_buffer,
        build_result,
    )

    timeout = msg.get("timeout", 120)
    trigger = msg.get("trigger")
    idle_timeout = msg.get("idle_timeout")
    idle_after_first = msg.get("idle_after_first_output", False)
    keep_ansi = msg.get("keep_ansi", False)
    explicit_timeout = msg.get("explicit_timeout", False)

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
            newline=msg.get("newline", False),
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
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    matched, reason = False, "cancelled"
                    break

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
                            matched, reason = True, "matched"
                            break

                if has_idle:
                    if snapshot != last_snapshot:
                        last_snapshot = snapshot
                        session.notify_snapshot_changed()

                if has_idle and session.check_snapshot_idle_timeout():
                    matched, reason = False, "idle_timeout"
                    break

                # GUI 窗口检测：节流 1s 主动轮询（后台监控线程另有 2s 兜底），
                # 检测到新窗口即短路返回（与子进程 wait_for_trigger 语义一致）
                gui = getattr(session, "_gui", None)
                if gui is not None:
                    now = time.time()
                    if now - _last_gui_check >= 1.0:
                        _last_gui_check = now
                        try:
                            gui.check(getattr(session, "_tracker", None), session.id)
                        except Exception:
                            pass
                    if gui.gui_windows and gui.detected_event.is_set():
                        gui.detected_event.clear()
                        matched, reason = False, "gui_detected"
                        break

                session.poll_natural_exit()
                if not session.running:
                    if session._is_real_crash():
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
        # exec 显式 --timeout：以完整 timeout 为等待预算等待命令自然结束，
        # 到期以 reason=timeout 返回并杀会话；其余（read 轮询 / exec 非显式）
        # 沿用固定短等待分片复查，会话继续运行、不杀
        if result_type == "exec" and explicit_timeout:
            deadline = time.time() + timeout
        else:
            if result_type == "exec":
                session.wait_for_initial_output(timeout=min(timeout, 2.0))
            deadline = time.time() + min(timeout, 1.0)
        # 分片复查：程序可能在等待期间结束/崩溃，应如实上报而非恒 ok；
        # 每片同步推进自然结束检测（监控线程按 2s 低频 tick 触发，仅等它
        # 扫到前会误判程序持续运行直至超时）
        while True:
            if cancel_event is not None and cancel_event.is_set():
                matched, reason = False, "cancelled"
                break
            remaining = deadline - time.time()
            if remaining <= 0:
                if result_type == "exec" and explicit_timeout:
                    matched, reason = False, "timeout"
                break
            if not session.running:
                if session._is_real_crash():
                    matched, reason = False, "crashed"
                else:
                    matched, reason = False, "ended"
                break
            session.poll_natural_exit()
            time.sleep(min(0.1, remaining))

    if result_type == "exec" and explicit_timeout and reason == "timeout":
        _kill_exec_session_on_timeout(ctx, session)

    if msg.get("snapshot_diff"):
        output = session.get_snapshot_diff(keep_ansi=keep_ansi)
    elif msg.get("full"):
        output = session.get_full_snapshot(keep_ansi=keep_ansi)
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
    from .handlers.utils import build_result, strip_if_needed

    idle_timeout = msg.get("idle_timeout")
    idle_after_first = msg.get("idle_after_first_output", False)
    explicit_timeout = msg.get("explicit_timeout", False)

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
    if result_type == "exec" and explicit_timeout and reason == "timeout":
        _kill_exec_session_on_timeout(ctx, session)
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
    _attach_subprocess_stderr(result, session, msg)
    if extra_fields:
        result.update(extra_fields)
    if send_response:
        Message.send(conn, result)
        session.clear_trigger()
    else:
        session.clear_trigger()
        return result, output


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
    from .handlers.utils import build_result, strip_if_needed

    idle_timeout = msg.get("idle_timeout")
    idle_after_first = msg.get("idle_after_first_output", False)
    explicit_timeout = msg.get("explicit_timeout", False)

    # 增量基准：必须在等待前捕获，才能返回等待期间的新增输出
    if from_offset is None:
        from_offset = 0 if msg.get("full") else session.output_offset

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
            timeout=msg.get("timeout", 120), cancel_event=cancel_event
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
            timeout=msg.get("timeout", 120), cancel_event=cancel_event
        )
        session.clear_trigger()
    else:
        matched, reason = False, "ok"

        def _on_cancel():
            nonlocal matched, reason
            matched, reason = False, "cancelled"

        _interruptible_sleep(min(msg.get("timeout", 120), 1.0), cancel_event, _on_cancel)

    if result_type == "exec" and explicit_timeout and reason == "timeout":
        _kill_exec_session_on_timeout(ctx, session)

    output = session.get_output(from_offset=from_offset, encoding=msg.get("encoding"))
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
    _attach_subprocess_stderr(result, session, msg)
    if extra_fields:
        result.update(extra_fields)
    if send_response:
        Message.send(conn, result)
    else:
        return result, output