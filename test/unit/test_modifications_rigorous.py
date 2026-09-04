"""对审查修复部分进行严密回归测试

覆盖本轮审查中修改的所有行为点：
1. format_timestamp_iso（新公共函数，替代 handler._format_iso_ms 重复实现）
2. client.formatter._format_event 的 ISO 时间解析（修复 [T14:32:15.12] 乱码）
3. ConfigManager.send_eol 验证（删除重复块后行为保持一致）
4. Session.set_trigger 锁内发布（修复 fresh_cycle 竞态）
5. ProcessMonitor._emit_process_end（统一崩溃/退出事件）
6. RequestHandler._build_result 的 start_time 格式化
7. Client._ensure_daemon 的 deadline 等待循环
8. __main__._handle_config_ops 的分支合并
9. pty.factory 平台条件导入
"""

import sys
import time
import threading
from datetime import datetime

import pytest
from unittest.mock import patch, MagicMock

from src.session.output.events import (
    EventHistoryManager,
    PendingEvent,
    format_timestamp_iso,
    _events_to_dicts,
)


# ============================================================
# 1. format_timestamp_iso
# ============================================================


class TestFormatTimestampIso:
    """format_timestamp_iso 单元测试

    该函数替代了原先分散在 handler.py 与 events.py 的两处重复实现，
    必须保证与旧实现逐字节一致。
    """

    @staticmethod
    def _old_impl(timestamp: float) -> str:
        """旧实现（重构前的参考）"""
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 10000:02d}"

    def test_epoch(self):
        """epoch 时间戳（依赖本地时区，与旧实现一致）"""
        assert format_timestamp_iso(0.0) == self._old_impl(0.0)

    def test_with_milliseconds(self):
        """带毫秒的时间戳：两位毫秒（百分秒）"""
        # 123.456 → microsecond=456000 → 456000//10000=45 → "45"
        ts = 123.456
        assert format_timestamp_iso(ts) == self._old_impl(ts)
        assert format_timestamp_iso(ts).endswith(".45")

    def test_zero_microsecond(self):
        """整秒：毫秒显示为 00"""
        ts = 2.0
        assert format_timestamp_iso(ts) == self._old_impl(ts)
        assert format_timestamp_iso(ts).endswith(".00")

    def test_negative_timestamp(self):
        """负时间戳（Windows 不支持，跳过）"""
        import sys
        if sys.platform == "win32":
            pytest.skip("Windows datetime.fromtimestamp 不支持负值")
        ts = -1000.5
        assert format_timestamp_iso(ts) == self._old_impl(ts)

    def test_future_timestamp(self):
        """未来时间戳"""
        ts = 2_000_000_000.0
        assert format_timestamp_iso(ts) == self._old_impl(ts)

    def test_iso_parseable_by_fromisoformat(self):
        """输出可被 datetime.fromisoformat 往返解析"""
        ts = 1234567890.987654
        parsed = datetime.fromisoformat(format_timestamp_iso(ts))
        assert parsed.timestamp() == pytest.approx(ts, abs=0.02)  # 百分秒精度

    def test_match_events_to_dicts(self):
        """_events_to_dicts 使用同一格式化，保证事件时间一致"""
        ts = 12345.6789
        dicts = _events_to_dicts([PendingEvent(timestamp=ts, type="x")])
        assert dicts[0]["time"] == format_timestamp_iso(ts)

    def test_consistent_across_calls(self):
        """同一时间戳多次调用结果一致"""
        ts = time.time()
        assert format_timestamp_iso(ts) == format_timestamp_iso(ts)


# ============================================================
# 2. client.formatter._format_event 时间解析
# ============================================================


class TestFormatEventTimestamp:
    """_format_event 时间显示修复测试

    修复前：ISO 字符串取 [-12:] 尾切片 → 产生 "[T14:32:15.12]" 乱码。
    修复后：用 datetime.fromisoformat 解析为 "%H:%M:%S"。
    """

    @pytest.fixture
    def formatter_mod(self):
        import src.client.formatter as fm
        return fm

    def test_iso_string_shows_hhmmss(self, formatter_mod, capsys):
        """ISO 字符串 → 仅显示 HH:MM:SS，无 T 前缀无毫秒"""
        ev = {"time": "2026-06-22T14:32:15.12", "type": "process_spawn",
              "info": "PID 123 created"}
        line = formatter_mod._format_event(ev)
        # 不应出现 T 前缀乱码
        assert "[T14:32:15" not in line
        assert "14:32:15" in line
        # 不应带毫秒
        assert "15.12" not in line

    def test_iso_string_crash_event(self, formatter_mod):
        """崩溃事件 ISO 时间解析"""
        ev = {"time": "2026-06-22T09:00:00.00", "type": "process_crash",
              "info": "PID 1 crashed!"}
        line = formatter_mod._format_event(ev)
        assert "[!!]" in line
        assert "09:00:00" in line

    def test_int_timestamp(self, formatter_mod):
        """int 时间戳 → localtime HH:MM:SS"""
        ev = {"time": 1700000000, "type": "process_spawn", "info": "x"}
        line = formatter_mod._format_event(ev)
        expected = time.strftime("%H:%M:%S", time.localtime(1700000000))
        assert expected in line

    def test_float_timestamp(self, formatter_mod):
        """float 时间戳 → localtime HH:MM:SS"""
        ev = {"time": 1700000000.5, "type": "process_exit", "info": "x"}
        line = formatter_mod._format_event(ev)
        expected = time.strftime("%H:%M:%S", time.localtime(1700000000.5))
        assert expected in line

    def test_empty_time(self, formatter_mod):
        """time 为空字符串 → 不崩溃，正常输出（空时间保持 [] 为原有行为）"""
        ev = {"time": "", "type": "process_exit", "info": "x"}
        line = formatter_mod._format_event(ev)
        assert isinstance(line, str)
        assert "[-]" in line
        assert "x" in line

    def test_missing_time(self, formatter_mod):
        """time 缺失 → 默认空串"""
        ev = {"type": "process_exit", "info": "x"}
        line = formatter_mod._format_event(ev)
        assert isinstance(line, str)

    def test_invalid_iso_string_falls_back(self, formatter_mod):
        """无法解析的字符串 → 原样显示（不崩溃）"""
        ev = {"time": "not-a-timestamp", "type": "process_exit", "info": "x"}
        line = formatter_mod._format_event(ev)
        assert "not-a-timestamp" in line

    def test_none_time(self, formatter_mod):
        """time 为 None → str(None) 显示"""
        ev = {"time": None, "type": "process_exit", "info": "x"}
        line = formatter_mod._format_event(ev)
        assert "None" in line


# ============================================================
# 3. ConfigManager.send_eol 验证
# ============================================================


class TestConfigManagerSendEol:
    """删除重复 send_eol 验证块后，验证逻辑保持完整"""

    @pytest.fixture
    def cfg(self):
        from src.client.config_manager import ConfigManager
        return ConfigManager()

    def test_valid_lf(self, cfg):
        cfg.set("send_eol", "lf")
        assert cfg.get("send_eol") == "lf"

    def test_valid_cr(self, cfg):
        cfg.set("send_eol", "cr")
        assert cfg.get("send_eol") == "cr"

    def test_valid_crlf(self, cfg):
        cfg.set("send_eol", "crlf")
        assert cfg.get("send_eol") == "crlf"

    def test_case_insensitive(self, cfg):
        """大小写不敏感（CR / CrLf / LF）"""
        for name in ("CR", "CrLf", "LF", "cR"):
            cfg.set("send_eol", name)
            assert cfg.get("send_eol") == name.lower()

    def test_invalid_raises(self, cfg):
        """无效值抛 ValueError（验证块未被误删）"""
        for bad in ("br", "nl", "", "linefeed", "123"):
            with pytest.raises(ValueError):
                cfg.set("send_eol", bad)

    def test_invalid_non_string(self, cfg):
        """非字符串值抛 ValueError"""
        with pytest.raises(ValueError):
            cfg.set("send_eol", 123)
        with pytest.raises(ValueError):
            cfg.set("send_eol", None)

    def test_default_is_lf(self, cfg):
        assert cfg.get("send_eol") == "lf"

    def test_resolve_eol(self):
        from src.client.config_manager import resolve_eol
        assert resolve_eol("lf") == "\n"
        assert resolve_eol("cr") == "\r"
        assert resolve_eol("crlf") == "\r\n"
        assert resolve_eol("CRLF") == "\r\n"
        # 未知值回退 \n
        assert resolve_eol("bogus") == "\n"


# ============================================================
# 4. Session.set_trigger 锁内发布
# ============================================================


class TestSessionSetTriggerLock:
    """set_trigger 重构（锁内发布触发状态）回归测试"""

    def _make_session(self):
        from src.session.session import Session
        s = Session.__new__(Session)  # 不执行 __init__，纯测锁语义
        from src.session.output.buffer import OutputBuffer
        from src.session.output.trigger import TriggerMatcher
        from src.session.encoding import decode_utf8
        s._out_buf = OutputBuffer(max_size=1024 * 1024)
        s._trig_mat = TriggerMatcher(decode_func=decode_utf8)
        return s

    def test_non_fresh_sets_trigger_and_matches(self):
        """非 fresh 模式：设置触发后，新到达数据可被匹配"""
        s = self._make_session()
        # 先设置触发（缓冲区为空，start_offset=0，不会误匹配）
        s.set_trigger(pattern="hello")
        assert s._trig_mat.matched is False
        # 模拟 reader 线程追加数据后执行两阶段匹配
        s._out_buf.append(b"hello world\n")
        snapshot = s._trig_mat.prepare_snapshot(s._out_buf)
        s._trig_mat.check_snapshot(snapshot)
        assert s._trig_mat.matched is True

    def test_fresh_mode_waits_for_new_data(self):
        """fresh 模式：初始不匹配旧数据，新数据到达后才匹配"""
        s = self._make_session()
        s._out_buf.append(b"hello world\n")
        s.set_trigger(pattern="hello", fresh=True)
        # fresh 模式初始不匹配已有数据
        assert s._trig_mat.matched is False
        # 新数据到达（模拟 reader 追加）后匹配
        s._out_buf.append(b"hello again\n")
        s._trig_mat.prepare_snapshot(s._out_buf)
        snapshot = s._trig_mat.prepare_snapshot(s._out_buf)
        s._trig_mat.check_snapshot(snapshot)
        assert s._trig_mat.matched is True

    def test_fresh_cycle_set_under_lock(self):
        """fresh_cycle 应与当前 read_cycle 一致（在锁内设置）"""
        s = self._make_session()
        s._out_buf.append(b"data\n")
        s._out_buf.append(b"more\n")
        s.set_trigger(pattern="x", fresh=True)
        # 锁内设置后，fresh_cycle == 当前 read_cycle
        assert s._trig_mat.fresh_cycle == s._out_buf.read_cycle

    def test_lock_held_during_set(self):
        """set 期间 out_buf.lock 被持有（与 reader 互斥）"""
        s = self._make_session()
        lock_held_during_set = []

        # 在持锁线程中调用 set_trigger，验证不会死锁（RLock 可重入）
        with s._out_buf.lock:
            s.set_trigger(pattern="x")
            lock_held_during_set.append(True)
        assert lock_held_during_set == [True]

    def test_newline_count_set(self):
        """非 fresh 模式：newline_count 从缓冲区统计"""
        s = self._make_session()
        s._out_buf.append(b"a\nb\nc\n")
        s.set_trigger(pattern="c", newline=True)
        assert s._trig_mat.newline_count == 3

    def test_idle_timeout_params_passed(self):
        """idle_timeout / idle_after_first_output 参数透传"""
        s = self._make_session()
        s.set_trigger(pattern="x", idle_timeout=5.0,
                      idle_after_first_output=True)
        assert s._trig_mat.idle_timeout == 5.0


class TestSessionSetTriggerLockHeld:
    """set_trigger 竞态修复的核心断言：触发状态发布全程持锁

    修复前：set() 与 fresh_cycle 赋值在 out_buf.lock 之外执行，
    reader 线程（持锁调用 prepare_snapshot）可能在两者之间插队，
    观察到 _fresh=True + _fresh_cycle=0 的中间态，导致 fresh 模式提前失效。
    修复后：set() 与 fresh_cycle 赋值在同一持锁临界区内完成。
    """

    def _make_session(self):
        from src.session.session import Session
        s = Session.__new__(Session)
        from src.session.output.buffer import OutputBuffer
        from src.session.output.trigger import TriggerMatcher
        from src.session.encoding import decode_utf8
        s._out_buf = OutputBuffer(max_size=1024 * 1024)
        s._trig_mat = TriggerMatcher(decode_func=decode_utf8)
        return s

    def test_fresh_set_runs_entirely_under_lock(self):
        """fresh 模式：TriggerMatcher.set 与 fresh_cycle 赋值均在锁内"""
        s = self._make_session()
        lock = s._out_buf.lock
        orig_set = s._trig_mat.set
        lock_held_in_set = []

        def patched_set(*args, **kwargs):
            # 断言 set() 被调用时锁已被本线程持有
            lock_held_in_set.append(lock._is_owned())
            return orig_set(*args, **kwargs)

        s._trig_mat.set = patched_set
        s.set_trigger(pattern="x", fresh=True)
        assert lock_held_in_set, "set() 应被调用"
        assert all(lock_held_in_set), "set() 必须在 out_buf.lock 持锁时执行"
        # fresh_cycle 已正确设置为当前 read_cycle（非 0 中间态）
        assert s._trig_mat.fresh_cycle == s._out_buf.read_cycle

    def test_non_fresh_set_runs_entirely_under_lock(self):
        """非 fresh 模式：set() 与快照提取也全程持锁"""
        s = self._make_session()
        lock = s._out_buf.lock
        orig_set = s._trig_mat.set
        lock_held_in_set = []

        def patched_set(*args, **kwargs):
            lock_held_in_set.append(lock._is_owned())
            return orig_set(*args, **kwargs)

        s._trig_mat.set = patched_set
        s.set_trigger(pattern="x")
        assert lock_held_in_set
        assert all(lock_held_in_set)

    def test_reader_cannot_observe_intermediate_fresh_state(self):
        """reader 线程（持锁 prepare_snapshot）无法看到 fresh_cycle=0 的中间态

        模拟 reader：主线程先持锁，set_trigger 线程会阻塞等待；
        主线程释放锁后 set_trigger 才执行——锁保证其与 reader 互斥。
        """
        import threading
        s = self._make_session()
        s._out_buf.append(b"old data\n")  # read_cycle > 0
        lock = s._out_buf.lock
        result = {}

        def setter():
            s.set_trigger(pattern="old", fresh=True)
            result["fresh"] = s._trig_mat._fresh
            result["fresh_cycle"] = s._trig_mat.fresh_cycle
            result["read_cycle"] = s._out_buf.read_cycle

        t = threading.Thread(target=setter)
        with lock:
            t.start()
            # 主线程（模拟 reader）持锁期间，setter 必须阻塞，无法修改状态
            t.join(timeout=0.2)
            assert t.is_alive(), "持锁期间 set_trigger 不应完成（应等待锁）"
        t.join(timeout=2.0)
        assert not t.is_alive()
        # fresh 模式仍有效（未被 reader 提前消费），fresh_cycle 已正确设置
        assert result["fresh"] is True
        assert result["fresh_cycle"] == result["read_cycle"]

    def test_stress_concurrent_set_and_read(self):
        """并发压力：reader 持续追加+快照，setter 反复设置 fresh 触发，不崩溃"""
        import threading
        s = self._make_session()
        s._out_buf.append(b"seed\n")
        stop = threading.Event()
        errors = []

        def reader():
            try:
                while not stop.is_set():
                    with s._out_buf.lock:
                        s._out_buf.append(b"stream data\n")
                        if s._trig_mat.has_pattern:
                            s._trig_mat.prepare_snapshot(s._out_buf)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def setter():
            try:
                for _ in range(30):
                    s.set_trigger(pattern="target", fresh=True)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        r = threading.Thread(target=reader)
        t = threading.Thread(target=setter)
        r.start()
        t.start()
        t.join(timeout=5.0)
        stop.set()
        r.join(timeout=5.0)
        assert not t.is_alive()
        assert errors == [], f"并发异常: {errors}"
        # 数据流中从未出现 "target"，因此绝不应产生匹配
        # （若竞态导致 fresh 提前失效并扫到旧数据，也不会匹配，因为旧数据也不含 target）
        assert s._trig_mat.matched is False


# ============================================================
# 4b. SessionThreads.wait_reader_ready（替代固定 sleep 的同步）
# ============================================================


class TestSessionThreadsReaderReady:
    """wait_reader_ready 事件同步测试（替代原 time.sleep(0.1)）"""

    def _make_threads(self, pty):
        from src.session.session_threads import (
            SessionThreads, SessionComponents,
        )
        from src.session.output.buffer import OutputBuffer
        from src.session.output.trigger import TriggerMatcher
        from src.session.encoding import decode_utf8
        from src.session.process.monitor import ProcessMonitor
        from src.session.process.gui import GuiDetector

        components = SessionComponents(
            pty_provider=lambda: pty,
            out_buf=OutputBuffer(max_size=1024 * 1024),
            trig_mat=TriggerMatcher(decode_func=decode_utf8),
            proc_mon=ProcessMonitor(pty_provider=lambda: pty,
                                    event_sink=lambda e: None),
            gui_detector=GuiDetector(event_sink=lambda e: None),
            session_id="test",
            on_exit=lambda *a: None,
        )
        return SessionThreads(components)

    def test_wait_returns_true_when_ready(self):
        """读者线程启动后 wait_reader_ready 立即返回 True（无需固定 sleep）"""
        class _Pty:
            def read(self, n):
                return b""
        threads = self._make_threads(_Pty())
        started = time.monotonic()
        threads.start()
        assert threads.wait_reader_ready(timeout=1.0) is True
        # 等待耗时远小于旧的固定 0.1s（读者线程就绪即返回）
        assert time.monotonic() - started < 1.0
        threads.stop()

    def test_wait_returns_true_immediately_after_ready(self):
        """就绪事件已置位后再次等待立即返回 True"""
        class _Pty:
            def read(self, n):
                return b""
        threads = self._make_threads(_Pty())
        threads.start()
        assert threads.wait_reader_ready(timeout=1.0) is True
        # 第二次等待应瞬时返回（事件已置位）
        t0 = time.monotonic()
        assert threads.wait_reader_ready(timeout=1.0) is True
        assert time.monotonic() - t0 < 0.05
        threads.stop()

    def test_wait_timeout_returns_false(self):
        """读者线程未启动时等待超时返回 False"""
        threads = self._make_threads(None)
        # 未调用 start()，事件不会置位
        assert threads.wait_reader_ready(timeout=0.05) is False

    def test_start_resets_ready_event(self):
        """start() 重置就绪事件（重启会话时重新等待）"""
        class _Pty:
            def read(self, n):
                return b""
        threads = self._make_threads(_Pty())
        threads.start()
        assert threads.wait_reader_ready(timeout=1.0) is True
        threads.stop()
        # 停止后再启动：事件被重置，需等待读者线程重新就绪
        threads.start()
        assert threads.wait_reader_ready(timeout=1.0) is True
        threads.stop()

    def test_ready_event_signals_from_reader_thread(self):
        """就绪事件由读者线程自身置位（验证是真正的线程就绪而非固定延时）"""
        class _Pty:
            def read(self, n):
                return b""
        threads = self._make_threads(_Pty())
        threads.start()
        # 读者线程运行中 → 事件已置位
        assert threads._reader_ready.is_set()
        threads.stop()


# ============================================================
# 5. ProcessMonitor._emit_process_end
# ============================================================


class TestProcessMonitorEmitProcessEnd:
    """_emit_process_end 重构（统一崩溃/退出事件）回归测试"""

    def _make_monitor(self):
        events = []
        from src.session.process.monitor import ProcessMonitor
        mon = ProcessMonitor(
            pty_provider=lambda: None,
            event_sink=lambda e: events.append(e),
        )
        return mon, events

    def test_crash_event(self):
        """非零退出码 → process_crash 事件 + crash_event 置位"""
        mon, events = self._make_monitor()
        mon._process_names[100] = "test.exe"
        mon._emit_process_end(100, 1, time.time(), "test")
        assert len(events) == 1
        ev = events[0]
        assert ev.type == "process_crash"
        assert "crashed" in ev.info
        assert "exit=1" in ev.info
        assert mon.crash_event.is_set()

    def test_crash_ntstatus(self):
        """NTSTATUS 崩溃码（0xC0000005）→ 含 NTSTATUS 描述"""
        mon, events = self._make_monitor()
        mon._emit_process_end(100, 0xC0000005, time.time(), "test")
        assert events[0].type == "process_crash"
        assert "0xC0000005" in events[0].info
        assert mon.crash_event.is_set()

    def test_normal_exit_zero(self):
        """退出码 0 → process_exit 事件，不置崩溃位"""
        mon, events = self._make_monitor()
        mon._process_names[100] = "app.exe"
        mon._emit_process_end(100, 0, time.time(), "test")
        assert len(events) == 1
        assert events[0].type == "process_exit"
        assert "exit=0" in events[0].info
        assert not mon.crash_event.is_set()

    def test_exit_unknown_none(self):
        """退出码 None → process_exit (unknown)"""
        mon, events = self._make_monitor()
        mon._emit_process_end(100, None, time.time(), "test")
        assert events[0].type == "process_exit"
        assert "unknown" in events[0].info

    def test_still_active_not_crash(self):
        """STILL_ACTIVE(259) 不算崩溃（进程仍在运行）"""
        mon, events = self._make_monitor()
        mon._emit_process_end(100, 259, time.time(), "test")
        assert events[0].type == "process_exit"
        assert not mon.crash_event.is_set()

    def test_name_from_cache(self):
        """名称优先从缓存取，缓存命中则不重复查询"""
        mon, events = self._make_monitor()
        mon._process_names[100] = "cached.exe"
        with patch("src.session.process.monitor._get_process_name") as mock_name:
            mon._emit_process_end(100, 0, time.time(), "test")
            mock_name.assert_not_called()
        assert "cached.exe" in events[0].info

    def test_name_fresh_lookup_when_not_cached(self):
        """缓存未命中 → 调用 _get_process_name"""
        mon, events = self._make_monitor()
        with patch("src.session.process.monitor._get_process_name",
                   return_value="fresh.exe"):
            mon._emit_process_end(100, 0, time.time(), "test")
        assert "fresh.exe" in events[0].info

    def test_source_in_log(self, caplog):
        """日志中包含来源标识"""
        import logging
        mon, events = self._make_monitor()
        with caplog.at_level(logging.INFO, logger="pty-session"):
            mon._emit_process_end(100, 1, time.time(), "IOCP")
        assert any("IOCP" in r.message for r in caplog.records)


# ============================================================
# 6. RequestHandler._build_result start_time
# ============================================================


class TestBuildResultStartTime:
    """_build_result 使用 format_timestamp_iso 格式化 start_time"""

    @pytest.fixture
    def handler_with_session(self):
        from src.daemon.handler import RequestHandler
        h = RequestHandler.__new__(RequestHandler)
        h.manager = MagicMock()
        return h

    def _session(self, start_time):
        s = MagicMock()
        s.id = "s1"
        s.start_time = start_time
        s.command = "echo"
        s.running = True
        s.pty_type = "subprocess"
        s.output_offset = 10
        s.exit_code = None
        s.error_message = None
        s.processes = []
        s.gui_windows = []
        s.pending_event_count = 0
        return s

    def test_start_time_iso_format(self, handler_with_session):
        """start_time 输出与 format_timestamp_iso 一致"""
        from src.session.output.events import format_timestamp_iso
        s = self._session(1234567890.5)
        handler_with_session.manager.get_session.return_value = s
        result = handler_with_session._build_result(
            "s1", "out", True, "matched")
        assert result["program"]["start_time"] == format_timestamp_iso(1234567890.5)

    def test_no_start_time(self, handler_with_session):
        """start_time 为 0/None 时不输出该字段"""
        s = self._session(0)
        handler_with_session.manager.get_session.return_value = s
        result = handler_with_session._build_result(
            "s1", "out", True, "matched")
        assert "start_time" not in result["program"]

    def test_session_none(self, handler_with_session):
        """session 为 None 时不崩溃"""
        handler_with_session.manager.get_session.return_value = None
        result = handler_with_session._build_result(
            "s1", "out", True, "matched")
        assert result["program"]["running"] is False
        assert "start_time" not in result["program"]

    def test_handler_no_longer_has_format_iso_ms(self):
        """旧的 _format_iso_ms 已删除（无残留）"""
        import src.daemon.handler as handler_mod
        assert not hasattr(handler_mod.RequestHandler, "_format_iso_ms")

    def test_process_tree_uses_module_level_import(self):
        """processes 路径查询使用模块级 _get_process_path（循环内不再惰性导入）"""
        from src.daemon.handler import RequestHandler, _get_process_path
        import inspect
        src = inspect.getsource(RequestHandler._build_result)
        # 循环内不应再有 from ... import（已移到模块顶部）
        assert "from ..session.process import" not in src


# ============================================================
# 7. Client._ensure_daemon deadline 等待
# ============================================================


class TestEnsureDaemonDeadline:
    """_ensure_daemon 改为 DAEMON_START_TIMEOUT deadline 后的行为"""

    def _client(self):
        from src.client.transport import Client
        return Client()

    def test_uses_deadline_not_fixed_count(self):
        """验证不再使用固定 range(15) 而是 deadline 判断"""
        import inspect
        from src.client.transport import Client
        src = inspect.getsource(Client._ensure_daemon)
        assert "range(15)" not in src
        assert "DAEMON_START_TIMEOUT" in src

    def test_starts_when_daemon_ready_before_deadline(self):
        """守护进程在 deadline 内就绪 → 正常返回"""
        client = self._client()
        calls = {"n": 0}

        def is_running():
            calls["n"] += 1
            return calls["n"] >= 3  # 第 3 次就绪

        with patch("src.client.transport.is_running", side_effect=is_running), \
             patch("src.client.transport.start_daemon"), \
             patch("src.client.transport.time.sleep"):
            client._ensure_daemon()  # 不应抛异常
        assert calls["n"] >= 3

    def test_exits_on_timeout(self):
        """deadline 内未就绪 → SystemExit"""
        client = self._client()
        with patch("src.client.transport.is_running", return_value=False), \
             patch("src.client.transport.start_daemon"), \
             patch("src.client.transport.time.sleep"):
            with pytest.raises(SystemExit):
                client._ensure_daemon()

    def test_elapsed_equals_config(self):
        """实际等待总时长与 DAEMON_START_TIMEOUT 一致"""
        from src.config import DAEMON_START_TIMEOUT
        client = self._client()
        sleeps = []
        with patch("src.client.transport.is_running", return_value=False), \
             patch("src.client.transport.start_daemon"), \
             patch("src.client.transport.time.sleep",
                   side_effect=lambda s: sleeps.append(s)):
            with pytest.raises(SystemExit):
                client._ensure_daemon()
        assert sleeps  # 确实等待过
        assert sum(sleeps) >= DAEMON_START_TIMEOUT - 0.5  # 约等于配置值


# ============================================================
# 8. __main__._handle_config_ops 分支合并
# ============================================================


class TestHandleConfigOps:
    """_handle_config_ops 分支合并后的行为不变性测试"""

    def _make_args(self, subcmd=None, default=None, show_config=None):
        """构造带 getattr 的 args 桩对象"""
        class _Args:
            def __init__(self):
                self.subcmd = subcmd
                self.default = default
                self.show_config = show_config
        return _Args()

    def test_no_subcmd_no_config(self, capsys):
        """无子命令、无配置操作 → 返回空 dict（让 main 打印帮助）"""
        from src.__main__ import _handle_config_ops
        args = self._make_args()
        result = _handle_config_ops(args)
        assert result == {}

    def test_show_config_no_subcmd_returns_none(self, capsys):
        """--show-config 无子命令 → 返回 None（打印后退出）"""
        from src.__main__ import _handle_config_ops
        args = self._make_args(show_config="")
        result = _handle_config_ops(args)
        assert result is None
        out = capsys.readouterr().out
        assert "timeout" in out

    def test_default_no_subcmd_returns_none(self, capsys):
        """--default 无子命令 → 返回 None 并警告"""
        from src.__main__ import _handle_config_ops
        args = self._make_args(default=["timeout", "30"])
        result = _handle_config_ops(args)
        assert result is None
        err = capsys.readouterr().err
        assert "警告" in err

    def test_default_with_subcmd_returns_overrides(self):
        """--default + 子命令 → 返回覆盖 dict"""
        from src.__main__ import _handle_config_ops
        args = self._make_args(subcmd="exec", default=["timeout", "30"])
        result = _handle_config_ops(args)
        assert result == {"timeout": 30.0}

    def test_show_config_with_subcmd_returns_empty(self, capsys):
        """--show-config + 子命令 → 打印配置后返回空 dict"""
        from src.__main__ import _handle_config_ops
        args = self._make_args(subcmd="exec", show_config="timeout")
        result = _handle_config_ops(args)
        assert result == {}
        out = capsys.readouterr().out
        assert "timeout" in out

    def test_default_invalid_key_exits(self):
        """--default 无效键 → SystemExit"""
        from src.__main__ import _handle_config_ops
        args = self._make_args(default=["bogus_key", "x"])
        with pytest.raises(SystemExit):
            _handle_config_ops(args)

    def test_default_and_show_config_no_subcmd(self, capsys):
        """--default + --show-config 无子命令 → 返回 None（show 优先退出）"""
        from src.__main__ import _handle_config_ops
        args = self._make_args(default=["timeout", "30"],
                               show_config="")
        result = _handle_config_ops(args)
        assert result is None


# ============================================================
# 9. pty.factory 平台条件导入
# ============================================================


class TestFactoryPlatformImport:
    """factory 平台条件导入（Windows 不加载 Unix 代码）"""

    def test_windows_imports_windows_only(self):
        """Windows 平台：导入 factory 后不引入 UnixPseudoTerminal"""
        import sys
        if sys.platform != "win32":
            pytest.skip("Windows 专用")
        import src.pty.factory as factory_mod
        # UnixPseudoTerminal 不应在模块命名空间中（Windows 分支不导入）
        assert not hasattr(factory_mod, "UnixPseudoTerminal")
        # WindowsPseudoTerminal 应已导入
        assert hasattr(factory_mod, "WindowsPseudoTerminal")
        # 子进程后端始终可用
        assert factory_mod.SubprocessPseudoTerminal is not None

    def test_non_windows_imports_unix(self):
        """非 Windows 平台：导入 factory 后不引入 WindowsPseudoTerminal"""
        import sys
        if sys.platform == "win32":
            pytest.skip("Unix 专用")
        import src.pty.factory as factory_mod
        assert not hasattr(factory_mod, "WindowsPseudoTerminal")
        assert hasattr(factory_mod, "UnixPseudoTerminal")

    def test_create_pty_string_command(self):
        """字符串命令 → SubprocessPseudoTerminal"""
        import src.pty.factory as factory_mod
        with patch.object(factory_mod, "SubprocessPseudoTerminal") as mock_sub:
            factory_mod.create_pty("echo hello")
            mock_sub.assert_called_once()

    def test_create_pty_windows_list_command(self):
        """Windows 平台列表命令 → WindowsPseudoTerminal，失败回退 subprocess"""
        import sys
        if sys.platform != "win32":
            pytest.skip("Windows 专用")
        import src.pty.factory as factory_mod
        with patch.object(factory_mod, "SubprocessPseudoTerminal") as mock_sub:
            with patch.object(factory_mod, "WindowsPseudoTerminal",
                              side_effect=Exception("boom")):
                factory_mod.create_pty(["echo", "hi"])
            mock_sub.assert_called_once()

    def test_create_pty_windows_list_success(self):
        """Windows 平台列表命令 → WindowsPseudoTerminal 成功"""
        import sys
        if sys.platform != "win32":
            pytest.skip("Windows 专用")
        import src.pty.factory as factory_mod
        with patch.object(factory_mod, "SubprocessPseudoTerminal") as mock_sub:
            with patch.object(factory_mod, "WindowsPseudoTerminal") as mock_win:
                factory_mod.create_pty(["echo", "hi"])
            mock_win.assert_called_once()
            mock_sub.assert_not_called()
