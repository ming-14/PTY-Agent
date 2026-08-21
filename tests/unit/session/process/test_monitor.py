"""session/process/monitor.py 单元测试（tracker 依赖版）"""

import time
import pytest

from src.process.monitor import ProcessMonitor
from src.process.base import ProcessNotification, NOTIF_SPAWN, NOTIF_EXIT, NOTIF_CRASH
from src.output.events import PendingEvent


class _MockTracker:
    """ProcessTreeTracker 最小 mock（实现端口所需方法）"""

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

    def test_host_process_gone_not_crash(self):
        """宿主进程（host_pids）退出码非 0 不触发崩溃判定（OpenConsole 正常退出码恒 1）"""
        events = []
        tracker = _MockTracker(
            pids=[1], exit_codes={2: 1}, host_pids={2}
        )
        mon = ProcessMonitor(
            tracker=tracker,
            event_sink=lambda e: events.append(e),
        )
        mon.reset(initial_pids={1, 2})
        mon._last_process_check_ms = 0
        mon.check_events()
        crash_events = [e for e in events if e.type == "process_crash"]
        exit_events = [e for e in events if e.type == "process_exit"]
        assert len(crash_events) == 0
        assert len(exit_events) == 0
        assert mon.crash_event.is_set() is False

    def test_host_process_excluded_from_new(self):
        """宿主进程不产生 spawn 事件（不在工作进程快照内）"""
        events = []
        tracker = _MockTracker(pids=[1, 2], host_pids={2})
        mon = ProcessMonitor(
            tracker=tracker,
            event_sink=lambda e: events.append(e),
        )
        mon.reset()
        mon._last_process_check_ms = 0
        mon.check_events()
        spawn_pids = [e.pid for e in events if e.type == "process_spawn"]
        assert spawn_pids == [1]
        assert 2 not in spawn_pids

    def test_pids_param_filters_host(self):
        """调用方传入含宿主进程的完整列表（pids 参数）时宿主被过滤"""
        events = []
        tracker = _MockTracker(host_pids={3})
        mon = ProcessMonitor(
            tracker=tracker,
            event_sink=lambda e: events.append(e),
        )
        mon.reset(initial_pids={1, 2, 3})
        mon._last_process_check_ms = 0
        mon.check_events(pids=[1])
        exit_pids = [e.pid for e in events if e.type == "process_exit"]
        assert exit_pids == [2]


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

    def test_host_process_notification_ignored(self):
        """宿主进程的 exit/crash 通知被过滤（OpenConsole 退出码 1 不误报崩溃）"""
        events = []
        tracker = _MockTracker(host_pids={99})
        tracker._notifications.append(
            ProcessNotification(NOTIF_EXIT, pid=99, exit_code=0))
        tracker._notifications.append(
            ProcessNotification(NOTIF_CRASH, pid=99, exit_code=1))
        mon = ProcessMonitor(
            tracker=tracker,
            event_sink=lambda e: events.append(e),
        )
        mon.drain_notifications()
        assert len(events) == 0
        assert mon.crash_event.is_set() is False

    def test_work_process_notification_kept(self):
        """非宿主进程（含宿主之外的退出码 1）仍产生对应事件"""
        events = []
        tracker = _MockTracker(host_pids={99})
        tracker._notifications.append(
            ProcessNotification(NOTIF_EXIT, pid=42, exit_code=0))
        mon = ProcessMonitor(
            tracker=tracker,
            event_sink=lambda e: events.append(e),
        )
        mon.drain_notifications()
        exit_events = [e for e in events if e.type == "process_exit"]
        assert len(exit_events) == 1
        assert exit_events[0].pid == 42


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
