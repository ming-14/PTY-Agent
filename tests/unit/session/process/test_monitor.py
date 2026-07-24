"""session/process/monitor.py 单元测试"""

import time
import pytest

from src.process.monitor import ProcessMonitor
from src.output.events import PendingEvent


class _MockPty:
    def __init__(self, pids=None, exit_codes=None):
        self._pids = pids or []
        self._exit_codes = exit_codes or {}

    def get_process_list(self):
        return self._pids

    def get_child_process_exit_code(self, pid):
        return self._exit_codes.get(pid)

    def get_job_notifications(self):
        return []


class TestProcessMonitorInit:
    def test_initial_state(self):
        events = []
        mon = ProcessMonitor(
            pty_provider=lambda: None,
            event_sink=lambda e: events.append(e),
        )
        assert mon.crash_event.is_set() is False


class TestProcessMonitorReset:
    def test_reset_with_pids(self):
        events = []
        mon = ProcessMonitor(
            pty_provider=lambda: None,
            event_sink=lambda e: events.append(e),
        )
        mon.reset(initial_pids={1, 2, 3})
        assert mon._last_pid_snapshot == {1, 2, 3}

    def test_reset_clears_crash(self):
        events = []
        mon = ProcessMonitor(
            pty_provider=lambda: None,
            event_sink=lambda e: events.append(e),
        )
        mon.crash_event.set()
        mon.reset()
        assert mon.crash_event.is_set() is False


class TestProcessMonitorCheckEvents:
    def test_detect_new_process(self):
        events = []
        pty = _MockPty(pids=[1, 2, 3])
        mon = ProcessMonitor(
            pty_provider=lambda: pty,
            event_sink=lambda e: events.append(e),
        )
        mon.reset(initial_pids={1})
        mon._last_process_check_ms = 0
        mon.check_events()
        spawn_events = [e for e in events if e.type == "process_spawn"]
        assert len(spawn_events) >= 1

    def test_detect_gone_process(self):
        events = []
        pty = _MockPty(pids=[1])
        mon = ProcessMonitor(
            pty_provider=lambda: pty,
            event_sink=lambda e: events.append(e),
        )
        mon.reset(initial_pids={1, 2})
        mon._last_process_check_ms = 0
        mon.check_events()
        exit_events = [e for e in events if e.type == "process_exit"]
        assert len(exit_events) >= 1


class TestProcessMonitorDrainNotifications:
    def test_no_notifications(self):
        events = []
        pty = _MockPty()
        mon = ProcessMonitor(
            pty_provider=lambda: pty,
            event_sink=lambda e: events.append(e),
        )
        mon.drain_notifications()


class TestProcessMonitorClearCrash:
    def test_clear_crash(self):
        events = []
        mon = ProcessMonitor(
            pty_provider=lambda: None,
            event_sink=lambda e: events.append(e),
        )
        mon.crash_event.set()
        mon.clear_crash()
        assert mon.crash_event.is_set() is False
