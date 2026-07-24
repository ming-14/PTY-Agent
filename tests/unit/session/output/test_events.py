"""session/output/events.py 单元测试"""

import time
import pytest

from src.output.events import PendingEvent, EventHistoryManager, _events_to_dicts


class TestPendingEvent:
    def test_create(self):
        ev = PendingEvent(timestamp=time.time(), type="process_spawn", pid=123, info="test")
        assert ev.type == "process_spawn"
        assert ev.pid == 123
        assert ev.info == "test"


class TestEventHistoryManager:
    def test_add_event(self):
        mgr = EventHistoryManager()
        ev = PendingEvent(timestamp=time.time(), type="process_spawn", pid=123)
        mgr.add_event(ev)
        assert mgr.pending_count == 1

    def test_add_events(self):
        mgr = EventHistoryManager()
        events = [
            PendingEvent(timestamp=time.time(), type="process_spawn", pid=1),
            PendingEvent(timestamp=time.time(), type="process_exit", pid=2),
        ]
        mgr.add_events(events)
        assert mgr.pending_count == 2

    def test_consume_all(self):
        mgr = EventHistoryManager()
        mgr.add_event(PendingEvent(timestamp=time.time(), type="process_spawn", pid=1))
        mgr.add_event(PendingEvent(timestamp=time.time(), type="process_exit", pid=2))
        result = mgr.consume_all()
        assert len(result) == 2
        assert mgr.pending_count == 0
        assert mgr.history_count == 2

    def test_get_all(self):
        mgr = EventHistoryManager()
        mgr.add_event(PendingEvent(timestamp=time.time(), type="process_spawn", pid=1))
        mgr.consume_all()
        mgr.add_event(PendingEvent(timestamp=time.time(), type="process_exit", pid=2))
        result = mgr.get_all()
        assert len(result) == 2

    def test_get_all_with_last(self):
        mgr = EventHistoryManager()
        for i in range(5):
            mgr.add_event(PendingEvent(timestamp=time.time(), type="process_spawn", pid=i))
        mgr.consume_all()
        result = mgr.get_all(last=2)
        assert len(result) == 2

    def test_get_all_with_since(self):
        now = time.time()
        mgr = EventHistoryManager()
        mgr.add_event(PendingEvent(timestamp=now - 10, type="process_spawn", pid=1))
        mgr.add_event(PendingEvent(timestamp=now - 5, type="process_exit", pid=2))
        mgr.add_event(PendingEvent(timestamp=now, type="gui_window", pid=3))
        mgr.consume_all()
        result = mgr.get_all(since=now - 6)
        assert len(result) == 2

    def test_clear(self):
        mgr = EventHistoryManager()
        mgr.add_event(PendingEvent(timestamp=time.time(), type="process_spawn", pid=1))
        mgr.consume_all()
        mgr.clear()
        assert mgr.pending_count == 0
        assert mgr.history_count == 0


class TestEventHistoryManagerListener:
    def test_add_event_listener(self):
        mgr = EventHistoryManager()
        received = []
        mgr.add_event_listener(lambda ev: received.append(ev))
        mgr.add_event(PendingEvent(timestamp=time.time(), type="process_spawn", pid=123))
        assert len(received) == 1
        assert received[0]["type"] == "process_spawn"
        assert received[0]["pid"] == 123

    def test_remove_event_listener(self):
        mgr = EventHistoryManager()
        received = []
        listener = lambda ev: received.append(ev)
        mgr.add_event_listener(listener)
        mgr.add_event(PendingEvent(timestamp=time.time(), type="process_spawn", pid=1))
        assert len(received) == 1
        mgr.remove_event_listener(listener)
        mgr.add_event(PendingEvent(timestamp=time.time(), type="process_exit", pid=2))
        assert len(received) == 1

    def test_listener_not_called_on_consume(self):
        mgr = EventHistoryManager()
        received = []
        mgr.add_event_listener(lambda ev: received.append(ev))
        mgr.add_event(PendingEvent(timestamp=time.time(), type="process_spawn", pid=1))
        mgr.consume_all()
        assert len(received) == 1

    def test_multiple_listeners(self):
        mgr = EventHistoryManager()
        received_a = []
        received_b = []
        mgr.add_event_listener(lambda ev: received_a.append(ev))
        mgr.add_event_listener(lambda ev: received_b.append(ev))
        mgr.add_event(PendingEvent(timestamp=time.time(), type="process_spawn", pid=1))
        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_duplicate_listener_not_added(self):
        mgr = EventHistoryManager()
        received = []
        listener = lambda ev: received.append(ev)
        mgr.add_event_listener(listener)
        mgr.add_event_listener(listener)
        mgr.add_event(PendingEvent(timestamp=time.time(), type="process_spawn", pid=1))
        assert len(received) == 1

    def test_listener_exception_does_not_break(self):
        mgr = EventHistoryManager()
        received = []
        def bad_listener(ev):
            raise RuntimeError("test error")
        mgr.add_event_listener(bad_listener)
        mgr.add_event_listener(lambda ev: received.append(ev))
        mgr.add_event(PendingEvent(timestamp=time.time(), type="process_spawn", pid=1))
        assert len(received) == 1

    def test_remove_nonexistent_listener_no_error(self):
        mgr = EventHistoryManager()
        mgr.remove_event_listener(lambda ev: None)


class TestEventsToDicts:
    def test_basic(self):
        events = [
            PendingEvent(timestamp=time.time(), type="process_spawn", pid=123, info="test"),
        ]
        result = _events_to_dicts(events)
        assert len(result) == 1
        assert result[0]["type"] == "process_spawn"
        assert result[0]["pid"] == 123
        assert "time" in result[0]

    def test_with_hwnd(self):
        events = [
            PendingEvent(timestamp=time.time(), type="gui_window", pid=0, hwnd=12345),
        ]
        result = _events_to_dicts(events)
        assert result[0]["hwnd"] == 12345

    def test_with_detail(self):
        events = [
            PendingEvent(timestamp=time.time(), type="process_spawn", pid=1, detail={"path": "/usr/bin/test"}),
        ]
        result = _events_to_dicts(events)
        assert result[0]["detail"]["path"] == "/usr/bin/test"
