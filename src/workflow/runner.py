"""Workflow 单次执行运行时 — WorkflowRun

维护一次 workflow 执行的完整状态：运行状态机、步骤状态、事件日志。
所有变更经锁保护，供执行线程写入、CLI 查询线程读取。
"""

from ..logging import get_logger
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional

_logger = get_logger("pty-daemon")

# 运行状态机
RUN_RUNNING = "running"
RUN_DONE = "done"
RUN_FAILED = "failed"
RUN_CANCELLED = "cancelled"

# 步骤状态机
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"
STEP_CANCELLED = "cancelled"

_FINISHED_STEP_STATES = (STEP_DONE, STEP_FAILED, STEP_SKIPPED, STEP_CANCELLED)

# 事件日志上限（deque 自动淘汰，避免头部 del O(n)）
_LOG_MAX = 2000


class WorkflowRun:
    """单次 workflow 执行（线程安全）"""

    def __init__(self, run_id: str, name: str, max_parallel: int,
                 step_output_limit: int):
        self.run_id = run_id
        self.name = name or run_id
        self.max_parallel = max_parallel
        self._step_output_limit = step_output_limit
        self.status: str = RUN_RUNNING
        self.steps: Dict[str, dict] = {}  # step_id → 步骤状态 dict
        self.log: Deque[dict] = deque(maxlen=_LOG_MAX)  # {time, message}
        self.cancel_event = threading.Event()
        self.started_at: float = time.time()
        self.finished_at: Optional[float] = None
        self.error: Optional[str] = None
        self._lock = threading.Lock()

    # ── 状态写入（执行线程） ──

    def _log(self, message: str):
        with self._lock:
            self.log.append({"time": time.time(), "message": message})

    def _step_entry(self, step_id: str) -> dict:
        """取步骤状态条目（无则创建含 id 的空条目）"""
        st = self.steps.setdefault(step_id, {})
        st.setdefault("id", step_id)
        st.setdefault("status", STEP_PENDING)
        st.setdefault("note", None)
        st.setdefault("output", "")
        st.setdefault("reason", None)
        st.setdefault("exit_code", None)
        st.setdefault("error", None)
        st.setdefault("attempts", None)
        st.setdefault("started_at", None)
        st.setdefault("ended_at", None)
        return st

    def mark_step_running(self, step_id: str):
        with self._lock:
            st = self._step_entry(step_id)
            st.update({
                "status": STEP_RUNNING,
                "started_at": time.time(),
                "ended_at": None,
            })
        self._log("步骤 %s 开始执行" % step_id)

    def mark_step_done(self, step_id: str, output: str, reason: Optional[str],
                       exit_code: Optional[int], note: Optional[str] = None,
                       attempts: Optional[int] = None):
        with self._lock:
            st = self._step_entry(step_id)
            st.update({
                "status": STEP_DONE,
                "output": output[-self._step_output_limit:],
                "reason": reason,
                "exit_code": exit_code,
                "error": None,
                "note": note,
                "attempts": attempts,
                "ended_at": time.time(),
            })
        self._log("步骤 %s 完成 (reason=%s)" % (step_id, reason))

    def mark_step_failed(self, step_id: str, error: str,
                         output: str = "", reason: Optional[str] = None,
                         attempts: Optional[int] = None):
        with self._lock:
            st = self._step_entry(step_id)
            st.update({
                "status": STEP_FAILED,
                "output": output[-self._step_output_limit:],
                "reason": reason,
                "error": error,
                "attempts": attempts,
                "ended_at": time.time(),
            })
        self._log("步骤 %s 失败: %s" % (step_id, error))

    def mark_step_skipped(self, step_id: str, note: str):
        with self._lock:
            st = self._step_entry(step_id)
            st.update({"status": STEP_SKIPPED, "note": note, "ended_at": time.time()})
        self._log("步骤 %s 已跳过: %s" % (step_id, note))

    def mark_step_cancelled(self, step_id: str, note: Optional[str] = None):
        with self._lock:
            st = self._step_entry(step_id)
            st.update({
                "status": STEP_CANCELLED,
                "note": note or "workflow cancelled",
                "ended_at": time.time(),
            })
        self._log("步骤 %s 已取消" % step_id)

    def step_status(self, step_id: str) -> str:
        with self._lock:
            return self.steps.get(step_id, {}).get("status", STEP_PENDING)

    def finish(self, status: str, error: Optional[str] = None):
        """终态写入：running → done/failed/cancelled（幂等）"""
        with self._lock:
            if self.status != RUN_RUNNING:
                return
            self.status = status
            self.error = error
            self.finished_at = time.time()
        if status == RUN_CANCELLED:
            self._log("workflow 已取消")
        elif status == RUN_FAILED:
            self._log("workflow 失败: %s" % error)
        else:
            self._log("workflow 完成")

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def cancel(self) -> None:
        """请求取消：置位取消事件，等待执行中的步骤响应"""
        self.cancel_event.set()
        self.finish(RUN_CANCELLED)

    # ── 状态读取（CLI 查询） ──

    def snapshot(self) -> dict:
        """序列化执行状态（workflow show / list 用）"""
        with self._lock:
            return {
                "runId": self.run_id,
                "name": self.name,
                "status": self.status,
                "maxParallel": self.max_parallel,
                "startedAt": self._fmt(self.started_at),
                "finishedAt": self._fmt(self.finished_at) if self.finished_at else None,
                "error": self.error,
                "steps": [dict(v) for v in self.steps.values()],
                "log": [dict(x) for x in self.log],
            }

    @staticmethod
    def _fmt(ts: float) -> str:
        from datetime import datetime

        return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")