"""Workflow DAG 调度引擎 — WorkflowEngine

按依赖图（depends_on，解析期已显式化）拓扑调度步骤：
- 依赖全部终态的步骤进入就绪集合，线程池并行执行
- if 条件（安全表达式）为假时跳过步骤
- 依赖失败/取消传播 → 步骤跳过；on_error=fail 终止整个 workflow
- 失败重试：retry 次数耗尽后按 on_error 处理
- cancel_event 置位时执行中的步骤（execution 层 0.1s 粒度）尽快返回

步骤执行复用 daemon/execution.py 的执行原语（与 exec/send/read handler 同源），
保证 workflow 内行为与 CLI 一致。
"""

import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Dict, Optional

from ..daemon.handlers.base import HandlerContext
from ..daemon.handlers.utils import build_result, filter_snapshot_lines
from .definition import ParsedStep, WorkflowDefinition
from .expr import ExpressionError, eval_expr, render_value
from .runner import (
    RUN_DONE,
    RUN_FAILED,
    STEP_CANCELLED,
    STEP_DONE,
    STEP_FAILED,
    STEP_SKIPPED,
    WorkflowRun,
)
from ..logging import get_logger

_logger = get_logger("pty-daemon")


def _parse_size(size_str: str) -> tuple:
    """解析终端尺寸字符串 WxH → (cols, rows)（与 client parse_terminal_size 同语义）"""
    s = str(size_str).lower().replace("×", "x")
    parts = s.split("x")
    if len(parts) != 2:
        raise ValueError("格式非法: %r，应为 WxH" % size_str)
    try:
        cols, rows = int(parts[0]), int(parts[1])
    except ValueError as e:
        raise ValueError("格式非法: %r，应为 WxH" % size_str) from e
    if cols <= 0 or rows <= 0:
        raise ValueError("尺寸必须为正数: %r" % size_str)
    return cols, rows


def _extract_result_fields(result: dict) -> dict:
    """从响应 dict 提取步骤结果核心字段（供 if 条件与 {{...}} 引用）

    Args:
        result: exec/send/read 响应 dict（build_result 产物）或 error 响应。
    """
    if not isinstance(result, dict):
        return {"output": "", "reason": None, "exit_code": None, "error": str(result)}
    error = result.get("error")
    return {
        "output": result.get("outputStream", ""),
        "reason": result.get("triggerReturnReason") or ("error" if error else "ok"),
        "exit_code": result.get("program", {}).get("exitCode"),
        "error": error,
    }


class _RunNamespace:
    """步骤结果命名空间（vars.* 与步骤 id 直引）

    对 results 做懒视图而非每次全量复制：results 只增不改（已完成步骤写入后
    不再变更），GIL 下字典并发读安全；vars 全程只读。避免第 k 步累计 O(N²) 复制。
    """

    __slots__ = ("_vars", "_results")

    def __init__(self, vars: Dict, results: Dict[str, dict]):
        self._vars = vars
        self._results = results

    def __getitem__(self, key: str):
        if key == "vars":
            return self._vars
        return self._results[key]

    def __contains__(self, key: str) -> bool:
        return key == "vars" or key in self._results


class WorkflowEngine:
    """workflow 依赖图调度器（exec 单实例，无状态）"""

    def run(self, ctx: HandlerContext, definition: WorkflowDefinition,
            run: WorkflowRun) -> None:
        """执行 workflow 定义（阻塞至全部步骤终态或取消/失败）"""
        _logger.info(
            "workflow %s 开始执行: %d 步骤, max_parallel=%d",
            run.run_id,
            len(definition.steps),
            definition.max_parallel,
        )
        try:
            self._schedule(ctx, definition, run)
        except Exception as e:
            _logger.exception("workflow %s 调度异常: %s", run.run_id, e)
            run.finish(RUN_FAILED, "engine error: %s" % e)
            return
        if not run.cancelled() and run.status != RUN_FAILED:
            run.finish(RUN_DONE)

    def _schedule(self, ctx, definition, run):
        steps = definition.steps
        steps_by_id = {s.id: s for s in steps}
        state = {s.id: "pending" for s in steps}
        results: Dict[str, dict] = {}
        fatal: Optional[str] = None
        # 共享命名空间：results 懒视图，全程单实例（worker 与调度线程同用）
        ns = _RunNamespace(definition.vars, results)
        executor = ThreadPoolExecutor(
            max_workers=definition.max_parallel,
            thread_name_prefix="workflow",
        )
        running = {}  # future → ParsedStep
        # Kahn 入度计数：就绪步骤只在依赖完成时产生（事件驱动，免每轮全量扫描）
        in_degree = {s.id: len(s.depends_on) for s in steps}
        dependents: Dict[str, list] = {s.id: [] for s in steps}
        for s in steps:
            for d in s.depends_on:
                dependents[d].append(s.id)
        ready = deque(s for s in steps if in_degree[s.id] == 0)

        def _notify_dependents(sid: str) -> None:
            """步骤进入终态：依赖该步骤的入度递减，归零者进入就绪"""
            for did in dependents[sid]:
                in_degree[did] -= 1
                if in_degree[did] == 0:
                    ready.append(steps_by_id[did])

        try:
            while True:
                if run.cancelled():
                    # 文档约定：已开始未完成的步骤标 cancelled（自行响应取消），
                    # 未开始的步骤标 skipped
                    for sid in list(state):
                        if state[sid] == "pending":
                            state[sid] = STEP_SKIPPED
                            run.mark_step_skipped(sid, "workflow cancelled")
                    break

                if fatal is not None:
                    # on_error=fail：终止调度，未开始步骤标记 skipped
                    for sid in list(state):
                        if state[sid] == "pending":
                            state[sid] = STEP_SKIPPED
                            run.mark_step_skipped(
                                sid, "workflow failed: %s" % (fatal or "")
                            )
                    run.finish(RUN_FAILED, fatal)
                    break

                # 派发就绪步骤（失败传播 + 条件判定；跳过/失败同样传播终态）
                while ready:
                    step = ready.popleft()
                    sid = step.id
                    bad = [d for d in step.depends_on
                           if state[d] in (STEP_FAILED, STEP_CANCELLED)]
                    if bad:
                        state[sid] = STEP_SKIPPED
                        run.mark_step_skipped(
                            sid, "dependency %s %s" % (bad[0], state[bad[0]])
                        )
                        _notify_dependents(sid)
                        continue
                    # 条件判定
                    if step.condition is not None:
                        try:
                            ok = bool(eval_expr(step.condition, ns))
                        except ExpressionError as e:
                            state[sid] = STEP_FAILED
                            run.mark_step_failed(sid, "if 条件求值失败: %s" % e)
                            _notify_dependents(sid)
                            if step.on_error == "fail":
                                fatal = "if 条件求值失败: %s" % e
                            continue
                        if not ok:
                            state[sid] = STEP_SKIPPED
                            run.mark_step_skipped(sid, "if 条件为假")
                            _notify_dependents(sid)
                            continue
                    if run.cancelled():
                        break
                    state[sid] = "running"
                    run.mark_step_running(sid)
                    fut = executor.submit(self._exec_step, ctx, run, step, ns)
                    running[fut] = step

                if fatal is not None:
                    continue  # 回到顶部统一处理 fatal

                if not running:
                    # 无运行中且无新派发：全部终态或防御性收敛检查
                    pending_left = [s for s in steps if state[s.id] == "pending"]
                    if not pending_left:
                        break
                    # 剩余 pending 必为依赖环（定义期已检测）或异常，防御标记
                    for s in pending_left:
                        state[s.id] = STEP_SKIPPED
                        run.mark_step_skipped(s.id, "不再可调度（依赖异常）")
                    break

                # 事件驱动：阻塞等待任一 future 完成（运行中的步骤自身对
                # cancel 0.1s 响应，完成后必返回，无需空转轮询）
                done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for fut in done:
                    step = running.pop(fut)
                    ret = fut.result()
                    state[step.id] = ret["status"]
                    if ret["status"] == STEP_DONE:
                        results[step.id] = ret["result"]
                    if ret["status"] == STEP_FAILED and step.on_error == "fail":
                        fatal = ret["error"] or "step failed: %s" % step.id
                    _notify_dependents(step.id)
        finally:
            # 取消/失败时等待在途步骤线程结束（execution 层 0.1s 响应）
            if running:
                _, not_done = wait(list(running), timeout=30.0)
                for fut in not_done:
                    fut.cancel()
            executor.shutdown(wait=False)

    def _exec_step(self, ctx, run, step: ParsedStep, ns: _RunNamespace) -> dict:
        """执行单个步骤（含重试），返回 {status, result, error}"""
        for attempt in range(step.retry + 1):
            if run.cancelled():
                run.mark_step_cancelled(step.id)
                return {"status": STEP_CANCELLED, "result": {}, "error": None}
            try:
                status, result, error = self._execute_once(ctx, run, step, ns)
            except Exception as e:
                _logger.exception(
                    "workflow %s 步骤 %s 异常: %s", run.run_id, step.id, e
                )
                status, result, error = STEP_FAILED, {}, str(e)
            if status == STEP_DONE:
                run.mark_step_done(
                    step.id,
                    result.get("output", ""),
                    result.get("reason"),
                    result.get("exit_code"),
                    attempts=attempt + 1,
                )
                return {"status": STEP_DONE, "result": result, "error": None}
            if status == STEP_CANCELLED:
                run.mark_step_cancelled(step.id, error or None)
                return {"status": STEP_CANCELLED, "result": {}, "error": error}
            if attempt < step.retry:
                _logger.info(
                    "workflow %s 步骤 %s 第 %d 次尝试失败，重试: %s",
                    run.run_id, step.id, attempt + 1, error,
                )
                self._sleep_non_cancel(run, step.retry_interval)
        run.mark_step_failed(step.id, error or "", attempts=attempt + 1)
        return {"status": STEP_FAILED, "result": {}, "error": error}

    def _sleep_non_cancel(self, run, seconds: float) -> None:
        """分段睡眠直至取消或被唤醒"""
        deadline = time.time() + seconds
        while time.time() < deadline and not run.cancelled():
            time.sleep(min(0.1, deadline - time.time()))

    def _execute_once(self, ctx, run, step, ns: _RunNamespace) -> tuple:
        """执行一次步骤（渲染插值后按类型分发）

        Returns:
            (status, result, error)：成功时 status=STEP_DONE 且 result 为
            核心字段 dict；失败时 status=STEP_FAILED 且 error 为描述。
        """
        raw = render_value(step.raw, ns)
        step_type = step.type

        if step_type == "exec":
            return self._exec_type(ctx, run, step, raw)
        if step_type == "send":
            return self._send_type(ctx, run, step, raw)
        if step_type == "read":
            return self._read_type(ctx, run, step, raw)
        if step_type == "kill":
            return self._kill_type(ctx, run, step, raw)
        return self._wait_type(run, step, raw)

    # ── 各步骤类型实现 ──

    def _get_session(self, ctx, session_id: str):
        """取会话（不存在或已结束时报错）"""
        session = ctx.manager.get_session(session_id)
        if session is None:
            raise RuntimeError("会话 '%s' 不存在" % session_id)
        if not session.running:
            raise RuntimeError("会话 '%s' 已结束" % session_id)
        return session

    def _exec_type(self, ctx, run, step, raw: dict) -> tuple:
        from ..daemon.execution import (
            _run_snapshot_flow,
            _run_subprocess_no_trigger_flow,
            _run_subprocess_trigger_flow,
        )

        session_id = raw["session"]
        command = raw["command"]
        cols, rows = raw.get("cols"), raw.get("rows")
        if raw.get("size") is not None:
            cols, rows = _parse_size(raw["size"])
        existing = ctx.manager.get_session(session_id)
        if existing:
            if not existing.running:
                raise RuntimeError("会话 '%s' 已结束（先 kill 再重新 exec）" % session_id)
            session = existing
        else:
            plugins = ctx.manager.match_auto_load(
                command, raw.get("cwd"), raw.get("env")
            )
            session = ctx.manager.create_session(
                session_id,
                command,
                encoding=raw.get("encoding"),
                cwd=raw.get("cwd"),
                env=raw.get("env"),
                cols=cols,
                rows=rows,
                plugins=plugins or None,
                mode=raw.get("mode", "pty"),
            )

        # 持有会话：exec 流程可能等待输出（子进程可能在等待期间快速退出，
        # 由 manager 触发 release_components）；hold 确保会话结束只延迟释放
        # 缓冲，最后一个 hold 退出时执行实际释放，避免流程读到 None 缓冲
        # （新建会话的创建期预持有在此消费）
        with session.hold():
            msg = {
                "timeout": raw.get("timeout", 120),
                "trigger": raw.get("trigger"),
                "idle_timeout": raw.get("idle_timeout"),
                "idle_after_first_output": raw.get("idle_after_first_output", False),
                "keep_ansi": raw.get("keep_ansi", False),
                "full": raw.get("full", False),
                "mode": raw.get("mode", "pty"),
                "size": raw.get("size"),
                "cwd": raw.get("cwd"),
                "env": raw.get("env"),
                "_t_start": time.monotonic(),
            }
            if getattr(session, "mode", "pty") == "subprocess":
                if msg.get("trigger"):
                    result, _ = _run_subprocess_trigger_flow(
                        ctx, None, session, msg, 0 if msg.get("full") else
                        session.output_offset, msg["trigger"], False, False,
                        msg.get("timeout", 120), start_offset=0,
                        result_type="exec", send_response=False,
                        cancel_event=run.cancel_event,
                    )
                else:
                    result, _ = _run_subprocess_no_trigger_flow(
                        ctx, None, session, msg, result_type="exec",
                        send_response=False, from_offset=0,
                        cancel_event=run.cancel_event,
                    )
            else:
                result, _ = _run_snapshot_flow(
                    ctx, None, session, msg, result_type="exec",
                    send_response=False, cancel_event=run.cancel_event,
                )
            return self._resolve_result(step, result)

    def _send_type(self, ctx, run, step, raw: dict) -> tuple:
        from ..daemon.execution import (
            _run_snapshot_flow,
            _run_subprocess_no_trigger_flow,
            _run_subprocess_trigger_flow,
        )

        session_id = raw["session"]
        session = self._get_session(ctx, session_id)
        input_text = raw["input"]
        # 持有会话：write_input 与后续等待输出期间会话可能自然结束，
        # hold 防止缓冲被提前释放（与 _exec_type 同理）
        with session.hold():
            session.write_input(input_text)
            trigger = raw.get("trigger")
            msg = {
                "timeout": raw.get("timeout", 120),
                "trigger": trigger,
                "idle_timeout": raw.get("idle_timeout"),
                "idle_after_first_output": raw.get("idle_after_first_output", False),
                "keep_ansi": raw.get("keep_ansi", False),
                "full": raw.get("full", False),
                "encoding": raw.get("encoding"),
                "_t_start": time.monotonic(),
            }
            is_sub = getattr(session, "mode", "pty") == "subprocess"
            if is_sub:
                if trigger:
                    result, _ = _run_subprocess_trigger_flow(
                        ctx, None, session, msg, 0 if msg.get("full") else
                        session.output_offset, trigger, False, True,
                        msg.get("timeout", 120), result_type="send",
                        send_response=False, cancel_event=run.cancel_event,
                    )
                else:
                    result, _ = _run_subprocess_no_trigger_flow(
                        ctx, None, session, msg, result_type="send",
                        send_response=False, from_offset=0 if msg.get("full")
                        else session.output_offset, cancel_event=run.cancel_event,
                    )
            else:
                result, _ = _run_snapshot_flow(
                    ctx, None, session, msg, result_type="send",
                    send_response=False, cancel_event=run.cancel_event,
                )
            return self._resolve_result(step, result)

    def _read_type(self, ctx, run, step, raw: dict) -> tuple:
        from ..daemon.execution import _run_snapshot_flow

        session_id = raw["session"]
        session = ctx.manager.get_session(session_id)
        if session is None:
            raise RuntimeError("会话 '%s' 不存在" % session_id)
        trigger = raw.get("trigger")
        # 持有会话：等待输出/构建响应期间会话可能自然结束（含多步骤
        # workflow 中上一步创建的会话在本步期间退出），hold 防止缓冲被
        # 提前释放（与 _exec_type 同理）
        with session.hold():
            msg = {
                "timeout": raw.get("timeout", 120),
                "trigger": trigger,
                "idle_timeout": raw.get("idle_timeout"),
                "idle_after_first_output": raw.get("idle_after_first_output", False),
                "keep_ansi": raw.get("keep_ansi", False),
                "full": raw.get("full", False),
                "snapshot_diff": raw.get("snapshot_diff", False),
                "encoding": raw.get("encoding"),
                "_t_start": time.monotonic(),
            }
            if trigger or raw.get("idle_timeout"):
                result, output = _run_snapshot_flow(
                    ctx, None, session, msg, result_type="read",
                    send_response=False, cancel_event=run.cancel_event,
                )
                if raw.get("lines") or raw.get("grep"):
                    output = filter_snapshot_lines(
                        output, raw.get("lines"), None, raw.get("grep")
                    )
                    result["outputStream"] = output
            else:
                if raw.get("snapshot_diff"):
                    output = session.get_snapshot_diff(
                        keep_ansi=raw.get("keep_ansi", False)
                    )
                elif raw.get("full"):
                    output = session.get_full_snapshot(
                        keep_ansi=raw.get("keep_ansi", False)
                    )
                else:
                    output = session.get_snapshot(keep_ansi=raw.get("keep_ansi", False))
                output = filter_snapshot_lines(
                    output, raw.get("lines"), None, raw.get("grep")
                )
                result = build_result(
                    ctx.manager,
                    session_id,
                    output,
                    False,
                    "ok" if session.running else "ended",
                    has_trigger=False,
                    result_type="read",
                    session=session,
                )
            return self._resolve_result(step, result)

    def _kill_type(self, ctx, run, step, raw: dict) -> tuple:
        session_id = raw["session"]
        if ctx.manager.get_session(session_id) is None:
            raise RuntimeError("会话 '%s' 不存在" % session_id)
        ctx.manager.remove_session(session_id)
        return STEP_DONE, {
            "output": "",
            "reason": "ok",
            "exit_code": None,
            "error": None,
        }, None

    def _wait_type(self, run, step, raw: dict) -> tuple:
        self._sleep_non_cancel(run, float(raw["seconds"]))
        if run.cancelled():
            return STEP_CANCELLED, {}, None
        return STEP_DONE, {
            "output": "",
            "reason": "ok",
            "exit_code": None,
            "error": None,
        }, None

    def _resolve_result(self, step, result) -> tuple:
        """把响应 dict 归一为 (status, 核心字段, error)

        取消（reason=cancelled）按取消处理；error 响应按失败处理；
        exec/send 步骤程序崩溃（program_crashed，含非 0 退出码的自然退出）
        按失败处理以触发 retry/on_error；read 步骤读已结束/崩溃会话属正常
        读取行为，不算失败；trigger 超时（trigger_timeout）按文档不算失败。
        """
        if result.get("error"):
            return STEP_FAILED, {}, result["error"]
        if result.get("triggerReturnReason") == "cancelled":
            return STEP_CANCELLED, {}, None
        fields = _extract_result_fields(result)
        if step.type != "read" and result.get("triggerReturnReason") == "program_crashed":
            _logger.info(
                "workflow 步骤 %s 程序崩溃（exit=%s），按失败处理",
                step.id,
                fields.get("exit_code"),
            )
            return STEP_FAILED, fields, "program crashed"
        return STEP_DONE, fields, None