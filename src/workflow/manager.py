"""Workflow 运行注册表 — WorkflowManager

持有全部 workflow 运行（含已结束），提供启动/查询/取消。
运行上限 WORKFLOW_MAX_RUNS，超限拒绝新 run。
启动在独立 daemon 线程执行（不阻塞 handler 线程）。
"""

import threading
import time
from typing import Dict, Optional

from ..config.daemon import (
    WORKFLOW_DEFAULT_PARALLEL,
    WORKFLOW_MAX_RUNS,
    WORKFLOW_STEP_OUTPUT_LIMIT,
)
from ..daemon.handlers.base import HandlerContext
from .definition import WorkflowDefinition
from .engine import WorkflowEngine
from .runner import RUN_CANCELLED, RUN_RUNNING, WorkflowRun
from ..logging import get_logger

_logger = get_logger("pty-daemon")


class WorkflowManager:
    """workflow 运行注册表（daemon 持有，线程安全）"""

    def __init__(self, session_manager, max_runs: Optional[int] = None,
                 default_parallel: Optional[int] = None,
                 step_output_limit: Optional[int] = None):
        self._session_manager = session_manager
        self._max_runs = max_runs if max_runs is not None else WORKFLOW_MAX_RUNS
        self._default_parallel = (
            default_parallel if default_parallel is not None
            else WORKFLOW_DEFAULT_PARALLEL
        )
        self._step_output_limit = (
            step_output_limit if step_output_limit is not None
            else WORKFLOW_STEP_OUTPUT_LIMIT
        )
        self._runs: Dict[str, WorkflowRun] = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._engine = WorkflowEngine()

    def start(self, definition: WorkflowDefinition,
              vars_override: Optional[dict] = None) -> WorkflowRun:
        """启动 workflow（后台线程执行），返回运行句柄

        Args:
            definition: 已解析校验的定义。
            vars_override: 调用方变量覆盖（CLI --vars），优先级高于定义 vars。

        Raises:
            ValueError: 运行数已达上限。
        """
        with self._lock:
            if len(self._runs) >= self._max_runs:
                # 容量满时自动淘汰最旧终态运行（FIFO）；全部运行中则拒绝
                finished = sorted(
                    (r for r in self._runs.values() if r.status != RUN_RUNNING),
                    key=lambda r: r.started_at,
                )
                if not finished:
                    raise ValueError(
                        "workflow 运行数已达上限 (%d)，请先等待执行完成"
                        % self._max_runs
                    )
                oldest = finished[0]
                del self._runs[oldest.run_id]
                _logger.info("workflow 运行数已达上限，淘汰最旧终态记录 %s", oldest.run_id)
            self._seq += 1
            run_id = "wf-%d-%d" % (int(time.time() * 1000), self._seq)
            run = WorkflowRun(
                run_id=run_id,
                name=definition.name,
                max_parallel=definition.max_parallel,
                step_output_limit=self._step_output_limit,
            )
            self._runs[run_id] = run

        if vars_override:
            definition.vars.update(vars_override)

        thread = threading.Thread(
            target=self._run_in_thread,
            args=(definition, run),
            name="workflow-%s" % run_id,
            daemon=True,
        )
        thread.start()
        _logger.info("workflow %s 已启动 (name=%r)", run_id, definition.name)
        return run

    def _run_in_thread(self, definition: WorkflowDefinition, run: WorkflowRun):
        try:
            ctx = HandlerContext(self._session_manager)
            self._engine.run(ctx, definition, run)
        except Exception:
            _logger.exception("workflow %s 执行线程异常", run.run_id)
            run.finish(RUN_CANCELLED if run.cancelled() else "failed",
                       "execution thread error")
        finally:
            _logger.info(
                "workflow %s 结束 (status=%s)", run.run_id, run.status
            )

    def list_runs(self) -> list:
        """所有运行（含已结束），按启动时间倒序"""
        with self._lock:
            runs = sorted(
                self._runs.values(), key=lambda r: r.started_at, reverse=True
            )
            return [
                {
                    "runId": r.run_id,
                    "name": r.name,
                    "status": r.status,
                    "startedAt": r.started_at,
                    "finishedAt": r.finished_at,
                    "stepCount": len(r.steps),
                }
                for r in runs
            ]

    def get_run(self, run_id: str) -> Optional[WorkflowRun]:
        with self._lock:
            return self._runs.get(run_id)

    def cancel_run(self, run_id: str) -> bool:
        """取消运行（幂等），返回是否找到该运行"""
        run = self.get_run(run_id)
        if run is None:
            return False
        if run.status == RUN_RUNNING:
            run.cancel()
            _logger.info("workflow %s 取消请求已发送", run_id)
        else:
            _logger.info("workflow %s 已处于终态 (%s)，无需取消", run_id, run.status)
        return True

    def remove_run(self, run_id: str) -> bool:
        """移除运行记录（仅终态可移除），返回是否移除"""
        run = self.get_run(run_id)
        if run is None:
            return False
        if run.status == RUN_RUNNING:
            return False
        with self._lock:
            self._runs.pop(run_id, None)
        _logger.info("workflow %s 记录已移除", run_id)
        return True