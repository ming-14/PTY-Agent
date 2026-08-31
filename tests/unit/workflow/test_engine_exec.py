"""workflow exec 步骤复用已结束会话的回归测试

BUG-3 回归：workflow exec 步骤对已自然结束（ended）的会话不得静默新建同名
会话重新执行新命令（CLI exec 侧用 check_ended_session 拒绝复用，workflow 此前
只查内存活跃表 get_session，会话结束移出后误走「新建」分支，造成意外副作用）。
"""

import pytest

from src.workflow.engine import WorkflowEngine
from src.workflow.definition import ParsedStep
from src.workflow.runner import WorkflowRun


class _FakeHistoryStore:
    def __init__(self, tag):
        self._tag = tag

    def get_session_tag(self, identifier):
        return self._tag


class _FakeManager:
    def __init__(self, tag=None, has_history_store=True):
        self.history_store = (
            _FakeHistoryStore(tag) if has_history_store else None
        )
        self.plugin_registry = None
        self._created = []

    def get_session(self, sid):
        # 已自然结束的会话不在内存活跃表
        return None

    def match_auto_load(self, command, cwd, env):
        return []

    def create_session(self, *args, **kwargs):
        # 记录被调用 = 走「新建」分支；抛哨兵避免继续执行完整流程
        self._created.append(args[0])
        raise KeyError("simulated-create")


class _FakeCtx:
    def __init__(self, manager):
        self.manager = manager


def _make_step(session_id="t1"):
    raw = {"session": session_id, "command": "echo hi", "timeout": 5}
    return ParsedStep(
        idx=0,
        id="s1",
        type="exec",
        raw=raw,
        depends_on=[],
        on_error="fail",
        retry=0,
        retry_interval=1.0,
        condition=None,
    )


def _run(engine, manager, step):
    run = WorkflowRun("run-1", "test", 2, 4096)
    ctx = _FakeCtx(manager)
    return engine._exec_type(ctx, run, step, step.raw)


class TestWorkflowExecRejectsEnded:
    """workflow exec 步骤：ended 历史拒绝，history/None 放行"""

    def test_ended_rejected_no_create(self):
        mgr = _FakeManager(tag="ended")
        with pytest.raises(RuntimeError, match="已结束"):
            _run(WorkflowEngine(), mgr, _make_step())
        assert mgr._created == []  # 未走「新建」分支，无副作用

    def test_history_allows_create(self):
        mgr = _FakeManager(tag="history")
        with pytest.raises(KeyError, match="simulated-create"):
            _run(WorkflowEngine(), mgr, _make_step())
        assert mgr._created == ["t1"]

    def test_no_history_allows_create(self):
        mgr = _FakeManager(tag=None)
        with pytest.raises(KeyError, match="simulated-create"):
            _run(WorkflowEngine(), mgr, _make_step())
        assert mgr._created == ["t1"]

    def test_no_history_store_allows_create(self):
        mgr = _FakeManager(has_history_store=False)
        with pytest.raises(KeyError, match="simulated-create"):
            _run(WorkflowEngine(), mgr, _make_step())
        assert mgr._created == ["t1"]
