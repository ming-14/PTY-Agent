"""EventHistoryManager 单元测试

测试事件历史管理器的添加、消费、查询、过滤、存在性检测、线程安全。
"""

import time
import threading
import pytest

from src.session.output.events import EventHistoryManager, PendingEvent, _events_to_dicts


class TestPendingEvent:
    """PendingEvent 数据类测试"""

    def test_create_spawn_event(self):
        ev = PendingEvent(timestamp=1.0, type="process_spawn", pid=100, info="created")
        assert ev.timestamp == 1.0
        assert ev.type == "process_spawn"
        assert ev.pid == 100
        assert ev.hwnd == 0

    def test_create_gui_event(self):
        ev = PendingEvent(timestamp=2.0, type="gui_window", pid=200, info="window",
                          hwnd=0x1234)
        assert ev.hwnd == 0x1234


class TestEventsToDicts:
    """_events_to_dicts 测试"""

    def test_conversion(self):
        events = [
            PendingEvent(timestamp=1.0, type="process_spawn", pid=100, info="a"),
            PendingEvent(timestamp=2.0, type="process_exit", pid=100, info="b"),
        ]
        dicts = _events_to_dicts(events)
        assert len(dicts) == 2
        assert dicts[0]["type"] == "process_spawn"
        assert dicts[1]["time"] == "1970-01-01T08:00:02.00"

    def test_empty(self):
        assert _events_to_dicts([]) == []


class TestEventHistoryManagerAdd:
    """EventHistoryManager.add_event 测试"""

    def test_add_single(self):
        mgr = EventHistoryManager()
        mgr.add_event(PendingEvent(timestamp=1.0, type="process_spawn", pid=100, info="a"))
        assert mgr.pending_count == 1

    def test_add_batch(self):
        mgr = EventHistoryManager()
        events = [
            PendingEvent(timestamp=float(i), type="process_spawn", pid=i, info=f"e{i}")
            for i in range(5)
        ]
        mgr.add_events(events)
        assert mgr.pending_count == 5


class TestEventHistoryManagerConsume:
    """EventHistoryManager.consume_all 测试"""

    def test_consume_moves_to_history(self):
        mgr = EventHistoryManager()
        mgr.add_event(PendingEvent(timestamp=1.0, type="process_spawn", pid=100, info="a"))
        result = mgr.consume_all()
        assert len(result) == 1
        assert mgr.pending_count == 0
        assert mgr.history_count == 1

    def test_consume_empty(self):
        mgr = EventHistoryManager()
        result = mgr.consume_all()
        assert result == []

    def test_consume_preserves_order(self):
        mgr = EventHistoryManager()
        mgr.add_event(PendingEvent(timestamp=1.0, type="process_spawn", pid=1, info="a"))
        mgr.add_event(PendingEvent(timestamp=2.0, type="process_exit", pid=2, info="b"))
        result = mgr.consume_all()
        assert result[0]["pid"] == 1
        assert result[1]["pid"] == 2


class TestEventHistoryManagerGetAll:
    """EventHistoryManager.get_all 测试"""

    def test_get_all_combined(self):
        mgr = EventHistoryManager()
        mgr.add_event(PendingEvent(timestamp=1.0, type="process_spawn", pid=1, info="h"))
        mgr.consume_all()
        mgr.add_event(PendingEvent(timestamp=2.0, type="process_exit", pid=2, info="p"))
        result = mgr.get_all()
        assert len(result) == 2

    def test_get_all_with_last(self):
        mgr = EventHistoryManager()
        for i in range(10):
            mgr.add_event(PendingEvent(timestamp=float(i), type="process_spawn", pid=i, info=f"e{i}"))
        result = mgr.get_all(last=3)
        assert len(result) == 3

    def test_get_all_with_since(self):
        mgr = EventHistoryManager()
        mgr.add_event(PendingEvent(timestamp=100.0, type="process_spawn", pid=1, info="old"))
        mgr.add_event(PendingEvent(timestamp=200.0, type="process_spawn", pid=2, info="new"))
        result = mgr.get_all(since=150.0)
        assert len(result) == 1
        assert result[0]["pid"] == 2

    def test_get_all_with_until(self):
        mgr = EventHistoryManager()
        mgr.add_event(PendingEvent(timestamp=100.0, type="process_spawn", pid=1, info="old"))
        mgr.add_event(PendingEvent(timestamp=200.0, type="process_spawn", pid=2, info="new"))
        result = mgr.get_all(until=150.0)
        assert len(result) == 1
        assert result[0]["pid"] == 1

    def test_get_all_does_not_consume(self):
        mgr = EventHistoryManager()
        mgr.add_event(PendingEvent(timestamp=1.0, type="process_spawn", pid=1, info="a"))
        mgr.get_all()
        assert mgr.pending_count == 1


class TestEventHistoryManagerCheckExistence:
    """EventHistoryManager.check_existence 测试"""

    def test_process_exit_always_false(self):
        mgr = EventHistoryManager()
        assert mgr.check_existence({"type": "process_exit", "pid": 100},
                                   pty_provider=lambda: None) is False

    def test_process_crash_always_false(self):
        mgr = EventHistoryManager()
        assert mgr.check_existence({"type": "process_crash", "pid": 100},
                                   pty_provider=lambda: None) is False

    def test_process_spawn_no_pty(self):
        mgr = EventHistoryManager()
        assert mgr.check_existence({"type": "process_spawn", "pid": 100},
                                   pty_provider=lambda: None) is False

    def test_process_spawn_zero_pid(self):
        mgr = EventHistoryManager()
        assert mgr.check_existence({"type": "process_spawn", "pid": 0},
                                   pty_provider=lambda: None) is False

    def test_unknown_type_false(self):
        mgr = EventHistoryManager()
        assert mgr.check_existence({"type": "unknown", "pid": 0},
                                   pty_provider=lambda: None) is False


class TestEventHistoryManagerClear:
    """EventHistoryManager.clear 测试"""

    def test_clear_empties_all(self):
        mgr = EventHistoryManager()
        mgr.add_event(PendingEvent(timestamp=1.0, type="process_spawn", pid=1, info="a"))
        mgr.consume_all()
        mgr.add_event(PendingEvent(timestamp=2.0, type="process_exit", pid=2, info="b"))
        mgr.clear()
        assert mgr.pending_count == 0
        assert mgr.history_count == 0


class TestEventHistoryManagerConcurrency:
    """EventHistoryManager 并发安全测试"""

    def test_concurrent_add_consume(self):
        mgr = EventHistoryManager()
        n_events = 100
        errors = []

        def producer():
            try:
                for i in range(n_events):
                    mgr.add_event(PendingEvent(
                        timestamp=float(i), type="process_spawn",
                        pid=1000 + i, info=f"e{i}",
                    ))
            except Exception as e:
                errors.append(e)

        def consumer():
            try:
                for _ in range(10):
                    mgr.consume_all()
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=producer)
        t2 = threading.Thread(target=consumer)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert len(errors) == 0