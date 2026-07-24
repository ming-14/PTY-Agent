"""session/output/trigger.py 单元测试"""

import time
import pytest

from src.output.trigger import TriggerMatcher, safe_regex_search


class _MockOutputBuffer:
    def __init__(self, data=b""):
        self._data = bytearray(data)
        self._read_cycle = 1

    @property
    def raw(self):
        return self._data

    @property
    def read_cycle(self):
        return self._read_cycle

    def count_byte(self, b):
        return self._data.count(b)


def _decode_utf8(data):
    return data.decode("utf-8", errors="replace")


class TestTriggerMatcherSet:
    def test_set_pattern(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set(">>>", buffer_length=0)
        assert tm.has_pattern is True
        assert tm.pattern == ">>>"

    def test_set_clear_pattern(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set(">>>", buffer_length=0)
        tm.clear()
        assert tm.has_pattern is False
        assert tm.pattern is None

    def test_set_invalid_regex_falls_back(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set("[invalid", buffer_length=0)
        assert tm.has_pattern is True
        assert tm._regex is None


class TestTriggerMatcherCheck:
    def test_check_match(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set("hello", buffer_length=0)
        buf = _MockOutputBuffer(b"hello world")
        result = tm.check(buf)
        assert result is True
        assert tm.matched is True

    def test_check_no_match(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set("xyz", buffer_length=0)
        buf = _MockOutputBuffer(b"hello world")
        result = tm.check(buf)
        assert result is False

    def test_check_regex_match(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set(r"prompt\s*>", buffer_length=0)
        buf = _MockOutputBuffer(b"prompt >")
        result = tm.check(buf)
        assert result is True

    def test_check_already_matched(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set("hello", buffer_length=0)
        buf = _MockOutputBuffer(b"hello")
        tm.check(buf)
        result = tm.check(buf)
        assert result is False

    def test_check_fresh_mode(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set("hello", fresh=True, buffer_length=0)
        tm.fresh_cycle = 0
        buf = _MockOutputBuffer(b"hello")
        buf._read_cycle = 0
        result = tm.check(buf)
        assert result is False

    def test_check_newline_mode(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set("hello", newline=True, buffer_length=0)
        tm.newline_count = 0
        buf = _MockOutputBuffer(b"hello\n")
        result = tm.check(buf)
        assert result is True


class TestTriggerMatcherIdleTimeout:
    def test_idle_timeout_not_set(self):
        tm = TriggerMatcher(_decode_utf8)
        assert tm.check_idle_timeout() is False

    def test_idle_timeout_not_elapsed(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set("x", buffer_length=0, idle_timeout=10.0)
        assert tm.check_idle_timeout() is False

    def test_idle_timeout_elapsed(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set("x", buffer_length=0, idle_timeout=0.0)
        time.sleep(0.01)
        assert tm.check_idle_timeout() is True

    def test_idle_after_first_output(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set("x", buffer_length=0, idle_timeout=0.01, idle_after_first_output=True)
        assert tm.check_idle_timeout() is False
        tm.on_data_appended(time.monotonic())
        time.sleep(0.02)
        assert tm.check_idle_timeout() is True


class TestSafeRegexSearch:
    def test_match(self):
        import re
        pattern = re.compile(r"hello")
        assert safe_regex_search(pattern, "hello world") is True

    def test_no_match(self):
        import re
        pattern = re.compile(r"xyz")
        assert safe_regex_search(pattern, "hello world") is False

    def test_redos_protection(self):
        import re
        pattern = re.compile(r"(a+)+b")
        result = safe_regex_search(pattern, "a" * 25, timeout=1.0)
        assert result is False


class TestSnapshotTrigger:
    def test_check_snapshot_match(self):
        tm = TriggerMatcher(decode_func=lambda x: x.decode("utf-8", errors="replace"))
        tm.set_snapshot_trigger(pattern=r"prompt>")
        assert tm.check_snapshot("hello prompt> world") is True
        assert tm.matched is True

    def test_check_snapshot_no_match(self):
        tm = TriggerMatcher(decode_func=lambda x: x.decode("utf-8", errors="replace"))
        tm.set_snapshot_trigger(pattern=r"prompt>")
        assert tm.check_snapshot("hello world") is False

    def test_check_snapshot_substring_fallback(self):
        tm = TriggerMatcher(decode_func=lambda x: x.decode("utf-8", errors="replace"))
        tm.set_snapshot_trigger(pattern="[invalid regex")
        assert tm.check_snapshot("text with [invalid regex inside") is True

    def test_check_snapshot_no_pattern(self):
        tm = TriggerMatcher(decode_func=lambda x: x.decode("utf-8", errors="replace"))
        tm.set_snapshot_trigger(idle_timeout=1.0)
        assert tm.check_snapshot("anything") is False

    def test_snapshot_idle_timeout_not_elapsed(self):
        tm = TriggerMatcher(decode_func=lambda x: x.decode("utf-8", errors="replace"))
        tm.set_snapshot_trigger(idle_timeout=5.0)
        tm.notify_snapshot_changed(time.monotonic())
        assert tm.check_idle_timeout() is False

    def test_snapshot_idle_timeout_elapsed(self):
        tm = TriggerMatcher(decode_func=lambda x: x.decode("utf-8", errors="replace"))
        tm.set_snapshot_trigger(idle_timeout=0.01)
        tm.notify_snapshot_changed(time.monotonic())
        time.sleep(0.05)
        assert tm.check_idle_timeout() is True

    def test_snapshot_idle_after_first_output(self):
        tm = TriggerMatcher(decode_func=lambda x: x.decode("utf-8", errors="replace"))
        tm.set_snapshot_trigger(idle_timeout=0.01, idle_after_first_output=True)
        assert tm.check_idle_timeout() is False
        tm.notify_snapshot_changed(time.monotonic())
        time.sleep(0.05)
        assert tm.check_idle_timeout() is True

    def test_snapshot_trigger_and_idle_combined(self):
        tm = TriggerMatcher(decode_func=lambda x: x.decode("utf-8", errors="replace"))
        tm.set_snapshot_trigger(pattern=r"done", idle_timeout=5.0)
        assert tm.check_snapshot("working...") is False
        assert tm.check_snapshot("all done!") is True
        assert tm.matched is True
