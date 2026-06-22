"""Session 事件系统单元测试

测试事件历史记录、get_all_events、check_event_existence 等新增功能。"""

import time
import threading

import pytest

from src.session.output.events import PendingEvent, _events_to_dicts


class TestPendingEvent:
    """PendingEvent 数据类基础测试"""

    def test_create_spawn_event(self):
        """测试创建 process_spawn 事件"""
        ev = PendingEvent(
            timestamp=1000.0,
            type="process_spawn",
            pid=1234,
            info="PID 1234 创建",
        )
        assert ev.timestamp == 1000.0
        assert ev.type == "process_spawn"
        assert ev.pid == 1234
        assert ev.info == "PID 1234 创建"
        assert ev.hwnd == 0

    def test_create_gui_event(self):
        """测试创建 gui_window 事件"""
        ev = PendingEvent(
            timestamp=2000.0,
            type="gui_window",
            pid=5678,
            info="测试窗口",
            hwnd=0x12345678,
        )
        assert ev.hwnd == 0x12345678

    def test_events_to_dicts(self):
        """测试 _events_to_dicts 模块函数"""
        events = [
            PendingEvent(timestamp=1.0, type="process_spawn", pid=100, info="created"),
            PendingEvent(timestamp=2.0, type="process_exit", pid=100, info="exited"),
        ]
        dicts = _events_to_dicts(events)
        assert len(dicts) == 2
        assert dicts[0]["type"] == "process_spawn"
        assert dicts[0]["pid"] == 100
        assert dicts[1]["time"] == "1970-01-01T08:00:02.00"


# 使用 mock PTY 避免真实依赖
class _MockPty:
    """模拟 PseudoTerminal"""

    def __init__(self):
        self._processes = [100, 200]
        self.drain_call_count = 0
        self.last_read_data = None

    def get_process_list(self):
        return list(self._processes)

    def get_exit_code(self):
        return 0

    def read(self, size):
        return b""

    def drain(self, max_bytes=65536):
        self.drain_call_count += 1
        return b""

    def close(self):
        pass

    def write(self, data):
        pass

    def poll_gui_windows(self):
        return []

    def get_child_process_exit_code(self, pid):
        return 0


class TestSessionEvents:
    """Session 事件系统测试"""

    @pytest.fixture
    def session(self, monkeypatch):
        """创建一个最小化 Session 实例（使用 mock PTY）"""
        from src.session.session import Session
        from src.pty.factory import create_pty

        # Mock create_pty 返回模拟 PTY
        def _mock_create_pty(*args, **kwargs):
            return _MockPty()

        monkeypatch.setattr("src.session.session.create_pty", _mock_create_pty)

        sess = Session("test-sess", "echo hello")
        sess.start()
        # 等待初始化
        time.sleep(0.2)
        yield sess
        sess.stop()

    def test_initial_empty_events(self, session):
        """测试初始状态无待处理事件"""
        events = session.consume_events()
        assert len(events) == 0

    def test_consume_events_moves_to_history(self, session):
        """测试 consume_events 将事件移入历史"""
        # 手动添加一个事件
        session.event_history.add_event(PendingEvent(
            timestamp=time.time(),
            type="process_spawn",
            pid=9999,
            info="手动添加",
        ))

        # 消费事件
        consumed = session.consume_events()
        assert len(consumed) == 1
        assert consumed[0]["type"] == "process_spawn"

        # 验证已移入历史
        assert session.event_history.history_count == 1
        assert session.event_history.history_events[0].type == "process_spawn"

        # 待处理队列应清空
        assert session.event_history.pending_count == 0

    def test_get_all_events_contains_history(self, session):
        """测试 get_all_events 返回历史+待处理事件"""

        # 添加两个历史事件
        session.event_history.add_event(PendingEvent(
            timestamp=100.0, type="process_spawn", pid=1, info="first",
        ))
        session.consume_events()  # 移入历史

        # 添加一个待处理事件
        session.event_history.add_event(PendingEvent(
            timestamp=200.0, type="process_exit", pid=2, info="second",
        ))

        # get_all_events 返回所有事件（不消费）
        all_ev = session.get_all_events()
        assert len(all_ev) == 2
        assert all_ev[0]["type"] == "process_spawn"  # 历史事件
        assert all_ev[1]["type"] == "process_exit"   # 待处理事件

        # 验证待处理事件未被消费
        assert session.event_history.pending_count == 1

    def test_consume_without_history_affects_pending_only(self, session):
        """测试 consume_events 只消费待处理事件"""

        # 添加历史事件
        session.event_history.add_event(PendingEvent(
            timestamp=100.0, type="process_spawn", pid=1, info="h1",
        ))
        session.consume_events()

        # 添加待处理事件
        session.event_history.add_event(PendingEvent(
            timestamp=200.0, type="process_exit", pid=2, info="p1",
        ))

        # 仅消费待处理
        pending = session.consume_events()
        assert len(pending) == 1
        assert pending[0]["type"] == "process_exit"

        # 历史应该包含两个事件
        assert session.event_history.history_count == 2

    def test_clear_on_start(self, session):
        """测试 start() 时清除历史记录"""

        # 添加事件到历史
        session.event_history.add_event(PendingEvent(
            timestamp=100.0, type="process_spawn", pid=1, info="test",
        ))
        session.consume_events()

        assert session.event_history.history_count == 1

        # 重启（start 会清空历史）
        session.stop()
        session.start()
        time.sleep(0.1)

        assert session.event_history.history_count == 0

    def test_check_event_existence_process_spawn(self, session):
        """测试 process_spawn 事件的存在检测"""
        # 进程在当前进程列表中的事件
        ev = {"type": "process_spawn", "pid": 100}  # MockPty 有 PID 100
        assert session.check_event_existence(ev) is True

        # 进程不在列表中的事件
        ev = {"type": "process_spawn", "pid": 99999}
        assert session.check_event_existence(ev) is False

    def test_check_event_existence_process_exit(self, session):
        """测试 process_exit 事件始终标记为不存在"""
        ev = {"type": "process_exit", "pid": 100}
        assert session.check_event_existence(ev) is False

    def test_check_event_existence_process_crash(self, session):
        """测试 process_crash 事件始终标记为不存在"""
        ev = {"type": "process_crash", "pid": 100}
        assert session.check_event_existence(ev) is False

    def test_check_event_existence_unknown_type(self, session):
        """测试未知类型事件返回 False"""
        ev = {"type": "unknown_type", "pid": 0}
        assert session.check_event_existence(ev) is False

    def test_check_event_existence_zero_pid(self, session):
        """测试 PID 为 0 时返回 False"""
        ev = {"type": "process_spawn", "pid": 0}
        assert session.check_event_existence(ev) is False

    def test_get_all_events_no_modification(self, session):
        """测试 get_all_events 不修改事件队列"""

        session.event_history.add_event(PendingEvent(
            timestamp=100.0, type="process_spawn", pid=1, info="evt",
        ))
        session.consume_events()

        session.event_history.add_event(PendingEvent(
            timestamp=200.0, type="process_exit", pid=1, info="evt2",
        ))

        # 多次调用 get_all_events 结果一致
        all1 = session.get_all_events()
        all2 = session.get_all_events()
        assert len(all1) == len(all2)
        assert [e["type"] for e in all1] == [e["type"] for e in all2]

    def test_crash_event_flag_on_crash(self, session):
        """测试检测到崩溃时 crash_event 被设置"""

        assert not session.process_monitor.crash_event.is_set()

        # 模拟 crash 检测逻辑：设置 crash_event
        session.process_monitor.crash_event.set()
        assert session.process_monitor.crash_event.is_set()

        # wait_for_trigger 应返回 "crashed"
        matched, reason = session.wait_for_trigger(timeout=0.1)
        assert reason in ("crashed",)

    def test_wait_for_trigger_returns_crashed(self, session, monkeypatch):
        """验证 wait_for_trigger 在崩溃时返回 reason=crashed"""

        # 确保没有其他事件干扰
        session.trigger_matcher.event.clear()
        session.process_monitor.crash_event.clear()

        # 手动触发 crash 事件
        session.process_monitor.crash_event.set()

        matched, reason = session.wait_for_trigger(timeout=5.0)
        assert reason == "crashed"
        assert matched is False

    def test_crash_event_cleared_on_start(self, session):
        """测试 start() 会清除 crash_event"""
        session.process_monitor.crash_event.set()
        assert session.process_monitor.crash_event.is_set()

        session.stop()
        session.start()

        assert not session.process_monitor.crash_event.is_set()

    def test_crash_event_cleared_on_clear_trigger(self, session):
        """测试 clear_trigger() 会清除 crash_event"""
        session.process_monitor.crash_event.set()
        assert session.process_monitor.crash_event.is_set()

        session.clear_trigger()
        assert not session.process_monitor.crash_event.is_set()

    def test_consume_events_concurrent_safety(self, session):
        """测试 consume_events 的线程安全性"""

        # 添加很多事件
        n_events = 50
        for i in range(n_events):
            session.event_history.add_event(PendingEvent(
                timestamp=float(i), type="process_spawn",
                pid=1000 + i, info=f"evt{i}",
            ))

        # 模拟多线程消费
        results = []
        errors = []

        def consumer():
            try:
                evts = session.consume_events()
                results.append(len(evts))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=consumer) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        total = sum(results)
        assert total == n_events


class TestSessionDrain:
    """Session _reader_loop 的 drain() 集成测试"""

    class _DataMockPty(_MockPty):
        """返回模拟数据并记录 drain 调用"""
        def __init__(self):
            super().__init__()
            self.read_count = 0

        def read(self, size):
            self.read_count += 1
            # 首次返回数据，之后返回 b"" 让 reader 退出
            if self.read_count == 1:
                return b"hello"
            return b""

        def drain(self, max_bytes=65536):
            self.drain_call_count += 1
            # 模拟排空得到额外数据
            return b" world"

    @pytest.fixture
    def drain_session(self, monkeypatch):
        """创建使用 _DataMockPty 的 Session"""
        from src.session.session import Session
        from src.pty.factory import create_pty

        mock_pty = self._DataMockPty()

        def _mock_create_pty(*args, **kwargs):
            return mock_pty

        monkeypatch.setattr("src.session.session.create_pty", _mock_create_pty)

        sess = Session("drain-test", "echo test")
        sess.start()
        time.sleep(0.3)
        yield sess, mock_pty
        sess.stop()

    def test_drain_called_after_read(self, drain_session):
        """_reader_loop 在 read() 后调用 drain()"""
        sess, mock_pty = drain_session
        # 读者线程至少执行了一次 read → drain 链
        assert mock_pty.drain_call_count >= 1
        assert mock_pty.read_count >= 1

    def test_drain_data_appended_to_buffer(self, drain_session):
        """drain() 返回的数据被追加到输出缓冲区"""
        sess, mock_pty = drain_session
        # 给读者线程足够时间处理
        time.sleep(0.2)
        with sess.output_buffer.lock:
            output = bytes(sess.output_buffer.raw)
        # "hello" + " world" = 11 字节
        assert len(output) >= 11
        assert b"hello world" in output
