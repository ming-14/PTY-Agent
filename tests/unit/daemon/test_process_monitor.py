"""ProcessMonitor 与 GuiDetector 单元测试（tracker 依赖版）

测试进程监控器的状态管理、崩溃检测、PID diff，以及 GUI 检测器的节流和事件发布。
"""

import time
import threading
import pytest

from src.process.monitor import ProcessMonitor
from src.process.gui import GuiDetector
from src.process.base import PendingEvent


class _MockTracker:
    """ProcessTreeTracker 最小 mock"""

    def __init__(self, pids=None, exit_codes=None, host_pids=None):
        self._pids = pids or []
        self._exit_codes = exit_codes or {}
        self._host_pids = set(host_pids or [])
        self._notifications = []

    def get_process_list(self):
        return self._pids

    def get_work_process_list(self):
        return [p for p in self._pids if p not in self._host_pids]

    def is_host_process(self, pid):
        return pid in self._host_pids

    def get_process_exit_code(self, pid):
        return self._exit_codes.get(pid)

    def drain_notifications(self):
        items = self._notifications
        self._notifications = []
        return items


class TestProcessMonitorInit:
    """ProcessMonitor 初始化测试"""

    def test_initial_state(self):
        """初始状态无崩溃"""
        events = []
        mon = ProcessMonitor(
            tracker=_MockTracker(),
            event_sink=lambda e: events.append(e),
        )
        assert not mon.crash_event.is_set()
        assert mon.process_names == {}

    def test_reset(self):
        """重置状态"""
        events = []
        mon = ProcessMonitor(
            tracker=_MockTracker(),
            event_sink=lambda e: events.append(e),
        )
        mon.crash_event.set()
        mon.reset(initial_pids={100, 200})
        assert not mon.crash_event.is_set()
        assert mon.process_names == {}

    def test_reset_with_initial_pids(self):
        """重置时设置初始 PID 快照"""
        events = []
        mon = ProcessMonitor(
            tracker=_MockTracker(),
            event_sink=lambda e: events.append(e),
        )
        mon.reset(initial_pids={100, 200})
        assert mon._last_pid_snapshot == {100, 200}

    def test_clear_crash(self):
        """清除崩溃事件"""
        events = []
        mon = ProcessMonitor(
            tracker=_MockTracker(),
            event_sink=lambda e: events.append(e),
        )
        mon.crash_event.set()
        mon.clear_crash()
        assert not mon.crash_event.is_set()


class TestProcessMonitorDrainNotifications:
    """ProcessMonitor.drain_notifications 测试"""

    def test_empty_notifications(self):
        """tracker 返回空通知列表"""
        events = []
        mon = ProcessMonitor(
            tracker=_MockTracker(),
            event_sink=lambda e: events.append(e),
        )
        mon.drain_notifications()
        assert len(events) == 0


class TestProcessMonitorCheckEvents:
    """ProcessMonitor.check_events 测试"""

    def test_empty_pid_snapshots(self):
        """空 PID 快照不产生事件"""
        events = []
        mon = ProcessMonitor(
            tracker=_MockTracker(pids=[]),
            event_sink=lambda e: events.append(e),
        )
        mon._last_process_check_ms = 0
        mon.check_events()
        assert len(events) == 0

    def test_new_process_spawn_event(self):
        """新进程产生 spawn 事件"""
        events = []
        mon = ProcessMonitor(
            tracker=_MockTracker(pids=[100, 200]),
            event_sink=lambda e: events.append(e),
        )
        mon.reset(initial_pids={100})
        mon._last_process_check_ms = 0
        mon.check_events()
        assert any(e.type == "process_spawn" for e in events)

    def test_gone_process_exit_event(self):
        """消失进程产生 exit 事件"""
        events = []
        mon = ProcessMonitor(
            tracker=_MockTracker(pids=[100], exit_codes={200: 0}),
            event_sink=lambda e: events.append(e),
        )
        mon.reset(initial_pids={100, 200})
        mon._process_names[200] = "test_proc"
        mon._last_process_check_ms = 0
        mon.check_events()
        assert any(e.type == "process_exit" and e.pid == 200 for e in events)


class TestGuiDetectorInit:
    """GuiDetector 初始化测试"""

    def test_initial_state(self):
        """初始状态无窗口"""
        events = []
        det = GuiDetector(event_sink=lambda e: events.append(e))
        assert det.gui_windows == []
        assert det.processes == []
        assert not det.detected_event.is_set()

    def test_clear(self):
        """清除状态"""
        events = []
        det = GuiDetector(event_sink=lambda e: events.append(e))
        det.gui_windows = [{"hwnd": 1}]
        det.processes = [100]
        det.detected_event.set()
        det.clear()
        assert det.gui_windows == []
        assert det.processes == []
        assert not det.detected_event.is_set()


class TestGuiDetectorCheck:
    """GuiDetector.check 测试"""

    def test_no_tracker(self):
        """无 tracker 时跳过"""
        events = []
        det = GuiDetector(event_sink=lambda e: events.append(e))
        det.check(None, "test")
        assert len(events) == 0

    def test_throttle(self):
        """节流：2 秒内不重复检测"""
        events = []
        det = GuiDetector(event_sink=lambda e: events.append(e))

        class _MockTracker:
            _call_count = 0
            def poll_gui_windows(self, pids=None):
                self._call_count += 1
                return []
            def get_process_list(self):
                return []

        tracker = _MockTracker()
        det.check(tracker, "test")
        det.check(tracker, "test")
        assert tracker._call_count == 1

    def test_new_window_publishes_event(self):
        """检测到新窗口发布事件"""
        events = []
        det = GuiDetector(event_sink=lambda e: events.append(e))
        det._last_poll_ms = 0

        class _MockTracker:
            def poll_gui_windows(self, pids=None):
                return [{"hwnd": 0x1234, "pid": 100, "title": "Test"}]
            def get_process_list(self):
                return [100]

        det.check(_MockTracker(), "test")
        assert len(events) == 1
        assert events[0].type == "gui_window"
        assert events[0].hwnd == 0x1234
        assert det.detected_event.is_set()
