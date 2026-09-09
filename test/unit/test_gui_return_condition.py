"""GUI 作为返回条件的单元测试

契约（重构后）：GUI 新窗口与 `-t` 触发正则是**平级返回条件**，等待期间
共同竞争，谁先命中谁先返回；不存在"设置了 -t 就压制 GUI 返回"的让位关系，
`gui_short_circuit` 开关已彻底删除。

同时锁定轮次基线语义：GUI 边沿以本轮武装时刻为基线，上一轮遗留的窗口
不得让本轮请求瞬间返回（那等于旧信息插队，破坏公平竞争）。窗口列表本身
保留，仍供 debug 呈现与 closewin 使用。
"""

import sys
import time

import pytest

from src.session.output.buffer import OutputBuffer
from src.session.output.trigger import TriggerMatcher
from src.session.output.events import PendingEvent
from src.session.process.gui import GuiDetector
from src.session.process.monitor import ProcessMonitor
from src.session.session import Session
from src.session.wake import WakeSignal

# 永不匹配的正则：让 -t 这一路肯定不会命中，用于隔离 GUI 条件
NEVER_MATCH = r"(?!x)x"

_WINDOW = {"hwnd": 0x1234, "pid": 100, "title": "Probe",
           "class_name": "TkTopLevel"}


class _PtyStub:
    """按轮次返回新增窗口的后端存根"""

    def __init__(self, windows=None):
        self.windows = list(windows or [])
        self.poll_count = 0

    def poll_gui_windows(self):
        self.poll_count += 1
        out, self.windows = self.windows, []
        return out

    def get_process_list(self):
        return [100]


def _make_session(windows=None):
    """装配一个无后台线程的 Session 骨架（确定性，专测等待语义）"""
    s = Session.__new__(Session)
    s.id = "gui-cond"
    s.running = True
    s._wake = WakeSignal()
    s._out_buf = OutputBuffer()
    s._trig_mat = TriggerMatcher(wake=s._wake)
    s._captured_events = []
    s._gui = GuiDetector(event_sink=lambda e: s._captured_events.append(e),
                         wake=s._wake)
    s._proc_mon = ProcessMonitor(pty_provider=lambda: s._pty,
                                 event_sink=lambda e: None,
                                 wake=s._wake)
    s._pty = _PtyStub(windows)
    return s


class TestGuiIsPeerCondition:
    """GUI 与 -t 平级：带 -t 的等待同样会被 GUI 命中打断"""

    def test_gui_returns_even_with_armed_trigger(self):
        """已武装永不匹配的 -t，GUI 新窗口仍应立刻成为返回原因"""
        s = _make_session(windows=[_WINDOW])
        s.set_trigger(pattern=NEVER_MATCH, start_idx=0)

        t0 = time.time()
        matched, reason = s.wait_for_trigger(timeout=30)
        elapsed = time.time() - t0

        assert matched is False
        assert reason == "gui_detected", (
            f"GUI 是一等返回条件，不应被 -t 压制：{matched}, {reason}")
        assert elapsed < 5, f"GUI 命中应快速返回，实际等待 {elapsed:.1f}s"

    def test_gui_hits_first_wins_over_late_trigger(self):
        """GUI 先到时先返回；-t 之后再命中不影响本轮结果"""
        s = _make_session(windows=[_WINDOW])
        s.set_trigger(pattern="ready", start_idx=0)
        matched, reason = s.wait_for_trigger(timeout=30)
        assert reason == "gui_detected"
        assert matched is False

    def test_trigger_wins_when_output_arrives_first(self):
        """反向：输出先命中 -t 时返回 matched，GUI 不抢占"""
        s = _make_session(windows=[])
        s.set_trigger(pattern="ready", start_idx=0)
        s._out_buf.append_text("ready\n")
        snapshot = s._trig_mat.prepare_snapshot(s._out_buf)
        s._trig_mat.check_snapshot(snapshot)

        matched, reason = s.wait_for_trigger(timeout=30)
        assert (matched, reason) == (True, "matched")

    def test_no_gui_returns_timeout(self):
        """无窗口无弹窗时按超时返回（不因新基线逻辑误报）"""
        s = _make_session(windows=[])
        s.set_trigger(pattern=NEVER_MATCH, start_idx=0)
        matched, reason = s.wait_for_trigger(timeout=0.5)
        assert (matched, reason) == (False, "timeout")


class TestRoundBaselineFairness:
    """轮次基线：上一轮遗留的窗口不得让本轮瞬间返回"""

    def test_stale_edge_does_not_jump_queue(self):
        """后台监控线程在武装前置位的边沿，本轮不应立即吃它返回"""
        s = _make_session(windows=[])
        s._gui.detected_event.set()          # 模拟上一轮遗留的边沿
        s.set_trigger(pattern=NEVER_MATCH, start_idx=0)   # 武装 = 建立基线

        t0 = time.time()
        matched, reason = s.wait_for_trigger(timeout=1.0)
        assert (matched, reason) == (False, "timeout"), (
            "set_trigger 应建立 GUI 基线并丢弃遗留边沿")
        assert time.time() - t0 >= 0.9, "不应被遗留边沿瞬间打断"

    def test_window_list_survives_arm(self):
        """arm 只清边沿，窗口列表必须保留（debug 呈现 + closewin 依赖它）"""
        s = _make_session(windows=[_WINDOW])
        s._gui.check(s._pty, s.id)
        assert s._gui.get_gui_windows(), "前置：应已记录窗口"

        s._gui.arm()
        assert not s._gui.detected_event.is_set()
        assert s._gui.get_gui_windows() == [_WINDOW], "arm 不得清空窗口列表"

    def test_edge_consumed_once(self):
        """边沿一次性消费：同一窗口不会连续打断两次请求"""
        s = _make_session(windows=[_WINDOW])
        s.set_trigger(pattern=NEVER_MATCH, start_idx=0)
        assert s.wait_for_trigger(timeout=30)[1] == "gui_detected"

        # 第二轮：窗口已消费且后端不再上报新增
        s._gui.arm()
        s.set_trigger(pattern=NEVER_MATCH, start_idx=0)
        assert s.wait_for_trigger(timeout=0.5)[1] == "timeout"

    def test_clear_trigger_resets_gui_edge(self):
        """clear_trigger 与 crash_event 对称：请求结束重置 GUI 边沿"""
        s = _make_session(windows=[])
        s._trig_mat.set(NEVER_MATCH)
        s._gui.detected_event.set()

        s.clear_trigger()
        assert not s._gui.detected_event.is_set()


class TestGuiDetectorApi:
    """GuiDetector 基线/消费原语自身的行为"""

    def test_consume_detection_clears_and_reports(self):
        det = GuiDetector(event_sink=lambda e: None)
        assert det.consume_detection() is False
        det.detected_event.set()
        assert det.consume_detection() is True
        assert det.consume_detection() is False

    def test_check_publishes_event_and_edge(self):
        events = []
        det = GuiDetector(event_sink=events.append)
        det.check(_PtyStub(windows=[_WINDOW]), "sid")
        assert len(events) == 1
        assert isinstance(events[0], PendingEvent)
        assert events[0].type == "gui_window"
        assert det.consume_detection() is True

    def test_arm_is_lock_free_and_idempotent(self):
        det = GuiDetector(event_sink=lambda e: None)
        det.detected_event.set()
        det.arm()
        det.arm()
        assert not det.detected_event.is_set()


class TestWaitForTriggerSignature:
    """gui_short_circuit 开关必须已彻底移除（防回归）"""

    def test_no_gui_short_circuit_param(self):
        import inspect
        from src.session.session import Session as _S

        params = inspect.signature(_S.wait_for_trigger).parameters
        assert "gui_short_circuit" not in params, (
            f"wait_for_trigger 不应再有让位开关：{list(params)}")
        assert set(params) == {"self", "timeout"}, list(params)

    def test_source_has_no_short_circuit_leftover(self):
        import inspect
        import pathlib

        src_root = pathlib.Path(inspect.getfile(
            __import__("src.session.session", fromlist=["Session"])))
        src_root = src_root.parent.parent   # .../src
        offenders = []
        for py in src_root.rglob("*.py"):
            if "gui_short_circuit" in py.read_text(encoding="utf-8"):
                offenders.append(py.name)
        assert not offenders, f"仍有残留：{offenders}"


class TestPresenterGuiPayload:
    """GUI 作为返回原因时，窗口信息是正载荷，不受 --no-debug 影响"""

    @staticmethod
    def _resp(reason):
        return {
            "type": "result", "session_id": "s1", "output": "out",
            "trigger_matched": False, "reason": reason,
            "program": {"mode": "subprocess", "running": True},
            "debug": {"gui_windows": [dict(_WINDOW)]},
        }

    def _combined(self, capsys):
        cap = capsys.readouterr()
        return cap.out + cap.err

    def test_gui_detected_shows_hwnd_without_debug(self, capsys):
        from src.client.presenters import print_response

        print_response(self._resp("gui_detected"), show_debug=False)
        combined = self._combined(capsys)

        assert "gui detected" in combined
        assert "0x00001234" in combined, (
            "返回原因就是 GUI 时必须给出 hwnd，否则 closewin 无从下手")

    def test_other_reason_keeps_no_debug_contract(self, capsys):
        """非 GUI 返回原因时，--no-debug 依旧隐藏窗口信息"""
        from src.client.presenters import print_response

        print_response(self._resp("timeout"), show_debug=False)
        assert "0x00001234" not in self._combined(capsys)

    def test_debug_mode_not_duplicated(self, capsys):
        """show_debug=True 时窗口信息只出现一次（不在正载荷里重复）"""
        from src.client.presenters import print_response

        print_response(self._resp("gui_detected"), show_debug=True)
        assert self._combined(capsys).count("0x00001234") == 1


class TestWakeSignal:
    """共享唤醒锚：GUI 与触发条件获得同量级的响应延迟"""

    def test_notify_wakes_waiter_promptly(self):
        import threading

        sig = WakeSignal()
        woke_at = []

        def waiter():
            sig.wait(5)
            woke_at.append(time.monotonic())

        th = threading.Thread(target=waiter)
        th.start()
        time.sleep(0.3)                 # 让 waiter 确实阻塞进去
        notified_at = time.monotonic()
        sig.notify()
        th.join(timeout=2)

        assert woke_at, "waiter 未被唤醒"
        lag = woke_at[0] - notified_at
        assert lag < 0.1, f"notify→唤醒延迟 {lag * 1000:.1f}ms 应远小于轮询周期"

    def test_reset_before_evaluate_does_not_lose_notify(self):
        """协议：生产者先发布状态再 notify，等待方先 reset 再判定 —— 不得丢唤醒"""
        sig = WakeSignal()
        sig.notify()
        sig.reset()
        assert sig.wait(0.05) is False      # 复位后是一次干净的等待
        sig.notify()
        assert sig.wait(1.0) is True

    def test_trigger_match_notifies_wake(self):
        sig = WakeSignal()
        tm = TriggerMatcher(wake=sig)
        buf = OutputBuffer()
        buf.append_text("hello world\n")
        tm.set("hello", start_idx=0)
        assert sig.triggered is False, "set 本身不该唤醒"
        snap = tm.prepare_snapshot(buf)
        tm.check_snapshot(snap)
        assert tm.matched and sig.triggered

    def test_substring_fallback_notifies_wake(self):
        sig = WakeSignal()
        tm = TriggerMatcher(wake=sig)
        buf = OutputBuffer()
        buf.append_text("value: [oops\n")
        tm.set("[oops", start_idx=0)            # 非法正则 → 子串回退
        snap = tm.prepare_snapshot(buf)
        tm.check_snapshot(snap)
        assert tm.matched is True
        assert sig.triggered is True

    def test_gui_publish_notifies_wake(self):
        sig = WakeSignal()
        det = GuiDetector(event_sink=lambda e: None, wake=sig)
        det._publish([dict(_WINDOW)])
        assert sig.triggered is True
        assert det.consume_detection() is True


class TestGuiEventListenerPath:
    """后端事件队列 → 检测器 → 唤醒 的接线"""

    def test_pending_drain_is_one_shot(self):
        det = GuiDetector(event_sink=lambda e: None)
        det._publish([dict(_WINDOW)])
        assert det.consume_detection() is True
        assert det.consume_detection() is False

    def test_check_without_wake_still_detects(self):
        """wake=None 时退化为轮询发现，行为不变（后向兼容）"""
        det = GuiDetector(event_sink=lambda e: None, wake=None)
        det._publish([dict(_WINDOW)])
        assert det.consume_detection() is True

    @pytest.mark.skipif(sys.platform != "win32",
                        reason="GuiWindowMonitor 仅 Windows 可用")
    def test_monitor_listener_fire_on_register(self):
        """订阅时已有待取窗口必须立即回调一次，避免订阅前丢事件"""
        from src.backend.windows.gui_monitor import (
            GuiWindowMonitor, GuiWindowInfo)

        m = GuiWindowMonitor(job=None)
        try:
            m._publish(GuiWindowInfo(**_WINDOW), source="test")
            taken = []
            m.set_listener(lambda: taken.extend(m.take_pending()))
            assert [w.title for w in taken] == ["Probe"], (
                "注册时应补触发一次，把订阅前已检出的窗口交出去")
            assert m.take_pending() == []
        finally:
            m.close()

    @pytest.mark.skipif(sys.platform != "win32",
                        reason="GuiWindowMonitor 仅 Windows 可用")
    def test_monitor_dedupe_on_hwnd(self):
        """去重发生在 _build_info：同一 hwnd 只产出一次"""
        from src.backend.windows.gui_monitor import GuiWindowMonitor

        m = GuiWindowMonitor(job=None)
        try:
            first = m._build_info(_WINDOW["hwnd"])
            second = m._build_info(_WINDOW["hwnd"])
            assert first is not None and first.hwnd == _WINDOW["hwnd"]
            assert second is None, "同一 hwnd 不应二次产出"
        finally:
            m.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
