"""session/process/monitor.py 单元测试（tracker 依赖版）"""

import time
import pytest

from src.process.monitor import ProcessMonitor
from src.process.base import ProcessNotification, NOTIF_SPAWN, NOTIF_EXIT, NOTIF_CRASH
from src.output.events import PendingEvent


class _MockTracker:
    """ProcessTreeTracker 最小 mock（实现端口所需方法）"""

    def __init__(self, pids=None, exit_codes=None):
        self._pids = pids or []
        self._exit_codes = exit_codes or {}
        self._notifications = []

    def get_process_list(self):
        return self._pids

    def get_process_exit_code(self, pid):
        return self._exit_codes.get(pid)

    def drain_notifications(self):
        items = self._notifications
        self._notifications = []
        return items


class TestProcessMonitorInit:
    def test_initial_state(self):
        events = []
        mon = ProcessMonitor(
            tracker=_MockTracker(),
            event_sink=lambda e: events.append(e),
        )
        assert mon.crash_event.is_set() is False


class TestProcessMonitorReset:
    def test_reset_with_pids(self):
        events = []
        mon = ProcessMonitor(
            tracker=_MockTracker(),
            event_sink=lambda e: events.append(e),
        )
        mon.reset(initial_pids={1, 2, 3})
        assert mon._last_pid_snapshot == {1, 2, 3}

    def test_reset_clears_crash(self):
        events = []
        mon = ProcessMonitor(
            tracker=_MockTracker(),
            event_sink=lambda e: events.append(e),
        )
        mon.crash_event.set()
        mon.reset()
        assert mon.crash_event.is_set() is False


class TestProcessMonitorCheckEvents:
    def test_detect_new_process(self):
        events = []
        tracker = _MockTracker(pids=[1, 2, 3])
        mon = ProcessMonitor(
            tracker=tracker,
            event_sink=lambda e: events.append(e),
        )
        mon.reset(initial_pids={1})
        mon._last_process_check_ms = 0
        mon.check_events()
        spawn_events = [e for e in events if e.type == "process_spawn"]
        assert len(spawn_events) >= 1

    def test_detect_gone_process(self):
        events = []
        tracker = _MockTracker(pids=[1])
        mon = ProcessMonitor(
            tracker=tracker,
            event_sink=lambda e: events.append(e),
        )
        mon.reset(initial_pids={1, 2})
        mon._last_process_check_ms = 0
        mon.check_events()
        exit_events = [e for e in events if e.type == "process_exit"]
        assert len(exit_events) >= 1

    def test_detect_crash_process(self):
        """gone 进程非 0 退出码 → process_crash + crash_event"""
        events = []
        tracker = _MockTracker(pids=[], exit_codes={2: 0xC0000005})
        mon = ProcessMonitor(
            tracker=tracker,
            event_sink=lambda e: events.append(e),
        )
        mon.reset(initial_pids={2})
        mon._last_process_check_ms = 0
        mon.check_events()
        crash_events = [e for e in events if e.type == "process_crash"]
        assert len(crash_events) == 1
        assert mon.crash_event.is_set() is True


class TestProcessMonitorDrainNotifications:
    def test_no_notifications(self):
        events = []
        tracker = _MockTracker()
        mon = ProcessMonitor(
            tracker=tracker,
            event_sink=lambda e: events.append(e),
        )
        mon.drain_notifications()

    def test_spawn_notification_event(self):
        """tracker spawn 通知 → process_spawn 事件"""
        events = []
        tracker = _MockTracker()
        tracker._notifications.append(
            ProcessNotification(NOTIF_SPAWN, pid=42, process_name="cmd.exe"))
        mon = ProcessMonitor(
            tracker=tracker,
            event_sink=lambda e: events.append(e),
        )
        mon.drain_notifications()
        spawn_events = [e for e in events if e.type == "process_spawn"]
        assert len(spawn_events) == 1
        assert spawn_events[0].pid == 42

    def test_exit_notification_event(self):
        """tracker exit 通知 → process_exit 事件"""
        events = []
        tracker = _MockTracker()
        tracker._notifications.append(
            ProcessNotification(NOTIF_EXIT, pid=42, exit_code=0))
        mon = ProcessMonitor(
            tracker=tracker,
            event_sink=lambda e: events.append(e),
        )
        mon.drain_notifications()
        exit_events = [e for e in events if e.type == "process_exit"]
        assert len(exit_events) == 1

    def test_crash_notification_event(self):
        """tracker crash 通知 → process_crash 事件 + crash_event"""
        events = []
        tracker = _MockTracker()
        tracker._notifications.append(
            ProcessNotification(NOTIF_CRASH, pid=42, exit_code=1))
        mon = ProcessMonitor(
            tracker=tracker,
            event_sink=lambda e: events.append(e),
        )
        mon.drain_notifications()
        crash_events = [e for e in events if e.type == "process_crash"]
        assert len(crash_events) == 1
        assert mon.crash_event.is_set() is True


class TestProcessMonitorClearCrash:
    def test_clear_crash(self):
        events = []
        mon = ProcessMonitor(
            tracker=_MockTracker(),
            event_sink=lambda e: events.append(e),
        )
        mon.crash_event.set()
        mon.clear_crash()
        assert mon.crash_event.is_set() is False
