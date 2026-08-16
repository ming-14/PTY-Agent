"""workflow 运行时单元测试 — 步骤状态 attempts 字段（retry 计数暴露）"""

from src.workflow.runner import STEP_FAILED, STEP_SKIPPED, WorkflowRun


def _make_run() -> WorkflowRun:
    return WorkflowRun("run-1", "test", 2, 4096)


class TestAttemptsField:
    """mark_step_done / mark_step_failed 记录尝试次数，snapshot 暴露"""

    def test_done_records_attempts(self):
        run = _make_run()
        run.mark_step_done("s1", "out", "ok", 0, attempts=1)
        step = {s["id"]: s for s in run.snapshot()["steps"]}["s1"]
        assert step["attempts"] == 1

    def test_failed_after_retries_records_attempts(self):
        run = _make_run()
        run.mark_step_failed("s1", "boom", attempts=3)
        step = {s["id"]: s for s in run.snapshot()["steps"]}["s1"]
        assert step["status"] == STEP_FAILED
        assert step["attempts"] == 3

    def test_defaults_to_none(self):
        run = _make_run()
        run.mark_step_running("s1")
        run.mark_step_done("s1", "", "ok", 0)  # 未显式传 attempts
        step = {s["id"]: s for s in run.snapshot()["steps"]}["s1"]
        assert step["attempts"] is None

    def test_skipped_never_attempted(self):
        run = _make_run()
        run.mark_step_skipped("s2", "if 条件为假")
        step = {s["id"]: s for s in run.snapshot()["steps"]}["s2"]
        assert step["status"] == STEP_SKIPPED
        assert step["attempts"] is None
