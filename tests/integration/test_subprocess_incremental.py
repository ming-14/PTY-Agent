"""子进程模式 handler 增量语义回归测试

验证 _run_subprocess_no_trigger_flow 在无 trigger 时按增量返回：
- 首次调用（from_offset=0）返回全量
- 再次调用（默认 from_offset=session.output_offset）只返回新增输出，不再重复旧内容
"""

import sys

import pytest

from src.session.session import Session
from src.daemon.handlers.exec_handler import _run_subprocess_no_trigger_flow


class _Manager:
    """最小 ctx.manager：build_result 仅需 get_session"""

    def __init__(self, s):
        self._s = s

    def get_session(self, sid):
        return self._s


class _Ctx:
    def __init__(self, s):
        self.manager = _Manager(s)


@pytest.fixture
def timed_session():
    """打印 A 后 sleep 2s 再打印 B 的子进程会话（mode=subprocess）"""
    code = "import time; print('A', flush=True); time.sleep(2); print('B', flush=True); time.sleep(2)"
    s = Session("sp-inc", [sys.executable, "-c", code], mode="subprocess")
    s.start()
    yield s
    try:
        s.stop()
    except Exception:
        pass


def test_no_trigger_incremental(timed_session):
    s = timed_session
    ctx = _Ctx(s)

    # 首次：from_offset=0 → 返回全量（此刻只有 A）
    r1, out1 = _run_subprocess_no_trigger_flow(
        ctx,
        None,
        s,
        {"timeout": 0.8, "explicit_timeout": True},
        result_type="exec",
        send_response=False,
        from_offset=0,
    )
    assert "A" in out1

    # 再次：默认 from_offset=session.output_offset → 增量，只捕获等待期间到达的 B
    r2, out2 = _run_subprocess_no_trigger_flow(
        ctx,
        None,
        s,
        {"timeout": 1.5, "explicit_timeout": True},
        result_type="exec",
        send_response=False,
    )
    assert "B" in out2
    assert "A" not in out2  # 增量：不重复旧内容


def test_no_trigger_full_returns_all(timed_session):
    """--full（from_offset=0）在已有会话上仍从 0 读全量"""
    import time

    s = timed_session
    ctx = _Ctx(s)

    _run_subprocess_no_trigger_flow(
        ctx,
        None,
        s,
        {"timeout": 0.8, "explicit_timeout": True},
        result_type="exec",
        send_response=False,
        from_offset=0,
    )
    # 轮询等待 B 输出到达（B 于 t≈2s 打印；负载高时启动/调度可能延迟，
    # 固定 sleep 会误判，必须等到 B 出现在缓冲再断言）
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if b"B" in s.output_buffer.get_slice(0):
            break
        time.sleep(0.1)
    r2, out2 = _run_subprocess_no_trigger_flow(
        ctx,
        None,
        s,
        {"timeout": 0.3, "explicit_timeout": True, "full": True},
        result_type="exec",
        send_response=False,
        from_offset=0,
    )
    assert "A" in out2 and "B" in out2


def test_no_trigger_intercall_no_loss(timed_session):
    """两次调用之间写入的输出不被跳过（stdout 消费游标兜底）

    回归：旧实现默认增量基准用写入末尾（output_offset），两次调用之间写入的
    输出会被永久跳过；新实现用消费游标（read_base），应能读到 B。
    """
    import time

    s = timed_session
    ctx = _Ctx(s)

    # 首次消费 A（交付到 A 末尾，推进游标）
    _run_subprocess_no_trigger_flow(
        ctx,
        None,
        s,
        {"timeout": 0.8, "explicit_timeout": True},
        result_type="exec",
        send_response=False,
        from_offset=0,
    )
    # 等 B 在两次调用之间产出（B 于 t≈2 打印；首调用已结束于 t≈0.8）
    time.sleep(2.2)
    # 默认（from_offset=None）→ 消费游标 → 读到 B，而不是被跳过
    r2, out2 = _run_subprocess_no_trigger_flow(
        ctx,
        None,
        s,
        {"timeout": 0.3, "explicit_timeout": True},
        result_type="exec",
        send_response=False,
    )
    assert "B" in out2