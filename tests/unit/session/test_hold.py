"""session 持有机制（pre_hold/acquire_hold/release_hold）单元测试

覆盖创建期预持有与 release_components 延迟释放的时序：
- pre_hold：create_session 在 start 前调用，把创建到首个 hold 的空窗并入持有
- acquire_hold：首个持有消费预持有（不再叠加计数）
- release_components：持有中置 pending，最后一个 hold 退出时实际释放
"""

import pytest

from src.session.session import Session


def _make_session(sid="hold-test"):
    return Session(sid, "cmd /c echo", mode="subprocess")


class TestPreHold:
    def test_pre_hold_sets_creation_hold(self):
        s = _make_session()
        s.pre_hold()
        assert s._creation_hold is True
        assert s._hold_count == 1

    def test_pre_hold_idempotent(self):
        s = _make_session()
        s.pre_hold()
        s.pre_hold()
        assert s._creation_hold is True
        assert s._hold_count == 1

    def test_acquire_hold_consumes_creation_hold(self):
        s = _make_session()
        s.pre_hold()
        s.acquire_hold()
        assert s._creation_hold is False
        assert s._hold_count == 1

    def test_acquire_hold_without_creation_hold(self):
        s = _make_session()
        s.acquire_hold()
        assert s._creation_hold is False
        assert s._hold_count == 1
        s.release_hold()
        assert s._hold_count == 0

    def test_hold_context_consumes_creation_hold(self):
        s = _make_session()
        s.pre_hold()
        with s.hold():
            assert s._creation_hold is False
            assert s._hold_count == 1
        assert s._hold_count == 0

    def test_release_creation_hold_restores_count(self):
        s = _make_session()
        s.pre_hold()
        s.release_creation_hold()
        assert s._creation_hold is False
        assert s._hold_count == 0

    def test_release_creation_hold_noop_after_consumed(self):
        s = _make_session()
        s.pre_hold()
        s.acquire_hold()
        s.release_creation_hold()
        assert s._creation_hold is False
        assert s._hold_count == 1
        s.release_hold()
        assert s._hold_count == 0


class TestReleasePending:
    def test_release_components_pending_while_held(self):
        s = _make_session()
        s.pre_hold()
        s._stop_finished = True
        with s.hold():
            s.release_components()
            assert s._release_pending is True
            assert s.output_buffer is not None
            assert s._out_buf is not None
        assert s._release_pending is False
        assert s._out_buf is None

    def test_release_components_immediate_without_hold(self):
        s = _make_session()
        s._stop_finished = True
        s.release_components()
        assert s._out_buf is None

    def test_release_components_requires_stop_finished(self):
        s = _make_session()
        s.release_components()
        assert s._out_buf is not None

    def test_creation_hold_bridges_gap_before_handler_hold(self):
        """缺陷2 竞态时序复现：子进程快速退出（reader 线程走完结束生命周期、
        release_components 时）handler 尚未进入 hold —— 预持有必须让释放转 pending"""
        s = _make_session()
        s.pre_hold()
        s._stop_finished = True
        s.release_components()
        assert s._release_pending is True
        assert s._out_buf is not None
        with s.hold():
            # 与 execution.py 崩溃点同路径：wait_for_initial_output 经
            # trigger.py 访问 _out_buf.first_output_event；修复前 _out_buf
            # 已被置 None 而 AttributeError
            assert s.wait_for_initial_output(timeout=0) is False
            assert s.output_buffer is not None
        assert s._out_buf is None

    def test_creation_hold_release_with_pending(self):
        """start 失败路径：撤销预持有时若有 pending 释放则立即执行"""
        s = _make_session()
        s.pre_hold()
        s._stop_finished = True
        s.release_components()
        assert s._release_pending is True
        s.release_creation_hold()
        assert s._creation_hold is False
        assert s._hold_count == 0
        assert s._release_pending is False
        assert s._out_buf is None

    def test_multiple_holds_need_all_exit(self):
        s = _make_session()
        s._stop_finished = True
        s.acquire_hold()
        s.acquire_hold()
        s.release_components()
        assert s._release_pending is True
        assert s._out_buf is not None
        s.release_hold()
        assert s._out_buf is not None
        s.release_hold()
        assert s._out_buf is None