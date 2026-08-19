"""等待循环统一判定点（resolve_exit_reason / check_gui_detected）单元测试

P0-B 判定单源化：crash/ended 与 GUI 检测收敛到 Session 单一判定点，
各等待循环（子进程 trigger / pty 快照 / 无 trigger）共用，行为零变化。
用最小桩对象验证，不依赖真实 PTY/线程。
"""

import time

from src.session.session.events import EventsMixin
from src.session.session.trigger import TriggerMixin


class _Event:
    def __init__(self, set_):
        self._set = set_

    def is_set(self):
        return self._set

    def clear(self):
        self._set = False


class _Gui:
    def __init__(self, windows, detected):
        self.gui_windows = windows
        self.detected_event = _Event(detected)

    def check(self, tracker, sid):
        pass


class _Stub(EventsMixin, TriggerMixin):
    """最小桩：仅暴露两个判定点依赖的属性和方法"""

    def __init__(self):
        self.id = "s1"
        self.exit_code = None
        self.error_message = None
        self._gui = None
        self._tracker = None
        self._events = []

    def get_all_events(self):
        return self._events


class TestResolveExitReason:
    def test_ended_by_default(self):
        assert _Stub().resolve_exit_reason() == "ended"

    def test_crashed_on_nonzero_exit(self):
        s = _Stub()
        s.exit_code = -1
        assert s.resolve_exit_reason() == "crashed"

    def test_crashed_on_error_message(self):
        s = _Stub()
        s.error_message = "boom"
        assert s.resolve_exit_reason() == "crashed"

    def test_crashed_on_crash_event(self):
        s = _Stub()
        s._events = [{"type": "process_crash", "detail": {"exitCode": 1}}]
        assert s.resolve_exit_reason() == "crashed"

    def test_ended_with_zero_exit(self):
        s = _Stub()
        s.exit_code = 0
        assert s.resolve_exit_reason() == "ended"


class TestCheckGuiDetected:
    def test_no_gui(self):
        s = _Stub()
        assert s.check_gui_detected(0.0) == (False, 0.0)

    def test_throttled_within_1s(self):
        s = _Stub()
        s._gui = _Gui([], False)
        now = time.time()
        assert s.check_gui_detected(now) == (False, now)

    def test_detected_when_enabled(self):
        s = _Stub()
        s._gui = _Gui(["win"], True)
        detected, _ = s.check_gui_detected(0.0)
        assert detected is True
        # 检测到即清空事件，避免重复上报
        assert not s._gui.detected_event.is_set()

    def test_not_reported_when_disabled(self):
        s = _Stub()
        s._gui = _Gui(["win"], True)
        detected, _ = s.check_gui_detected(0.0, enabled=False)
        assert detected is False
        # 未启用短路时保留轮询但不清空事件（对齐既有时序）
        assert s._gui.detected_event.is_set()
