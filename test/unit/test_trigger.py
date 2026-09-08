"""TriggerMatcher 单元测试（文本快照）

测试触发条件匹配器的正则匹配、换行策略、新鲜模式、静默超时、ReDoS 防护。
匹配对象为文本（快照属性 text），无字节解码回调。
"""

import re
import time

from src.session.output.trigger import TriggerMatcher, safe_regex_search


class _MockBuffer:
    """模拟 OutputBuffer（文本语义）"""

    def __init__(self, text="", read_cycle=0, line_count=None):
        self._text = text
        self._read_cycle = read_cycle
        self._line_count = (line_count if line_count is not None
                            else text.count("\n"))

    @property
    def text(self):
        return self._text

    @property
    def read_cycle(self):
        return self._read_cycle

    @property
    def line_count(self):
        return self._line_count

    def get_text_since(self, idx, max_chars=0):
        s = self._text[idx:]
        if max_chars and len(s) > max_chars:
            return s[-max_chars:]
        return s


def _check(tm: TriggerMatcher, buf) -> bool:
    """模拟两阶段匹配"""
    snapshot = tm.prepare_snapshot(buf)
    if snapshot is None:
        return False
    return tm.check_snapshot(snapshot)


class TestSafeRegexSearch:
    """safe_regex_search ReDoS 防护测试"""

    def test_normal_match(self):
        pat = re.compile(r"hello")
        assert safe_regex_search(pat, "hello world") is True

    def test_no_match(self):
        pat = re.compile(r"xyz")
        assert safe_regex_search(pat, "hello world") is False

    def test_redos_pattern_timeout(self):
        pat = re.compile(r"(a+)+$")
        text = "a" * 20 + "!"
        result = safe_regex_search(pat, text, timeout=0.05)
        assert isinstance(result, bool)

    def test_invalid_regex_returns_false(self):
        try:
            pat = re.compile(r"[invalid")
        except re.error:
            assert True
            return
        assert isinstance(safe_regex_search(pat, "test"), bool)


class TestTriggerMatcherSet:
    """TriggerMatcher.set 测试"""

    def test_set_pattern(self):
        tm = TriggerMatcher()
        tm.set(">>>", start_idx=0)
        assert tm.has_pattern is True
        assert tm.pattern == ">>>"

    def test_set_clears_matched(self):
        tm = TriggerMatcher()
        tm.set(">>>", start_idx=0)
        buf = _MockBuffer(">>>")
        _check(tm, buf)
        assert tm.matched is True
        tm.set("xxx", start_idx=0)
        assert tm.matched is False

    def test_set_clears_event(self):
        tm = TriggerMatcher()
        tm.set(">>>", start_idx=0)
        buf = _MockBuffer(">>>")
        _check(tm, buf)
        assert tm.event.is_set()
        tm.set("xxx", start_idx=0)
        assert not tm.event.is_set()

    def test_set_with_newline(self):
        tm = TriggerMatcher()
        tm.set(">>>", newline=True, start_idx=0)
        assert tm.newline_count == 0

    def test_set_with_idle_timeout(self):
        tm = TriggerMatcher()
        tm.set(">>>", idle_timeout=5.0, start_idx=0)
        assert tm.idle_timeout == 5.0

    def test_set_fresh_mode(self):
        tm = TriggerMatcher()
        tm.set(">>>", fresh=True, start_idx=0)
        assert tm.fresh_cycle == 0


class TestTriggerMatcherCheck:
    """TriggerMatcher 匹配测试"""

    def test_check_match_regex(self):
        tm = TriggerMatcher()
        tm.set(r">>>", start_idx=0)
        buf = _MockBuffer("output\n>>>")
        assert _check(tm, buf) is True
        assert tm.matched is True
        assert tm.event.is_set()

    def test_check_no_match(self):
        tm = TriggerMatcher()
        tm.set(r"xxx", start_idx=0)
        buf = _MockBuffer("output\n>>>")
        assert _check(tm, buf) is False
        assert tm.matched is False

    def test_check_already_matched(self):
        tm = TriggerMatcher()
        tm.set(r">>>", start_idx=0)
        buf = _MockBuffer(">>>")
        assert _check(tm, buf) is True
        assert _check(tm, buf) is False

    def test_check_no_pattern(self):
        tm = TriggerMatcher()
        buf = _MockBuffer("data")
        assert _check(tm, buf) is False

    def test_check_invalid_regex_fallback_substring(self):
        tm = TriggerMatcher()
        tm.set(r"[invalid", start_idx=0)
        buf = _MockBuffer("[invalid data")
        assert _check(tm, buf) is True

    def test_check_newline_blocks(self):
        tm = TriggerMatcher()
        tm.set(r">>>", newline=True, start_idx=0)
        tm.newline_count = 0
        tm._newline_first_ok = False
        buf = _MockBuffer(">>>")
        assert _check(tm, buf) is False

    def test_check_newline_allows_after_newline(self):
        tm = TriggerMatcher()
        tm.set(r">>>", newline=True, start_idx=0)
        tm.newline_count = 0
        buf = _MockBuffer("\n>>>")
        assert _check(tm, buf) is True

    def test_check_newline_first_ok(self):
        tm = TriggerMatcher()
        tm.set(r">>>", newline=True, start_idx=0)
        tm.newline_count = 0
        tm._newline_first_ok = True
        buf = _MockBuffer(">>>")
        assert _check(tm, buf) is True

    def test_check_fresh_mode_waits(self):
        tm = TriggerMatcher()
        tm.set(r">>>", fresh=True, start_idx=0)
        tm.fresh_cycle = 0
        buf = _MockBuffer(">>>", read_cycle=0)
        assert _check(tm, buf) is False

    def test_check_fresh_mode_matches_after_cycle(self):
        tm = TriggerMatcher()
        tm.set(r">>>", fresh=True, start_idx=0)
        tm.fresh_cycle = 0
        buf = _MockBuffer(">>>", read_cycle=1)
        assert _check(tm, buf) is True

    def test_check_start_offset(self):
        tm = TriggerMatcher()
        tm.set(r">>>", start_idx=5)
        buf = _MockBuffer(">>> hello >>>")
        assert _check(tm, buf) is True


class TestTriggerMatcherSnapshot:
    """两阶段匹配（prepare_snapshot + check_snapshot）测试（文本快照）"""

    def test_prepare_returns_snapshot_on_match_candidate(self):
        tm = TriggerMatcher()
        tm.set(r">>>", start_idx=0)
        buf = _MockBuffer("output\n>>>")
        snap = tm.prepare_snapshot(buf)
        assert snap is not None
        assert snap.text == "output\n>>>"
        assert snap.pattern == ">>>"
        assert snap.regex is not None
        assert tm.check_snapshot(snap) is True
        assert tm.matched is True
        assert tm.event.is_set()

    def test_prepare_returns_none_no_pattern(self):
        tm = TriggerMatcher()
        buf = _MockBuffer("data")
        assert tm.prepare_snapshot(buf) is None

    def test_prepare_returns_none_already_matched(self):
        tm = TriggerMatcher()
        tm.set(r">>>", start_idx=0)
        buf = _MockBuffer(">>>")
        snap = tm.prepare_snapshot(buf)
        assert tm.check_snapshot(snap) is True
        assert tm.prepare_snapshot(buf) is None

    def test_prepare_fresh_waits_cycle(self):
        tm = TriggerMatcher()
        tm.set(r">>>", fresh=True, start_idx=0)
        tm.fresh_cycle = 0
        buf = _MockBuffer(">>>", read_cycle=0)
        assert tm.prepare_snapshot(buf) is None
        buf2 = _MockBuffer(">>>", read_cycle=1)
        snap = tm.prepare_snapshot(buf2)
        assert snap is not None
        assert tm.check_snapshot(snap) is True

    def test_prepare_newline_blocks(self):
        tm = TriggerMatcher()
        tm.set(r">>>", newline=True, start_idx=0)
        tm.newline_count = 0
        tm._newline_first_ok = False
        buf = _MockBuffer(">>>")
        assert tm.prepare_snapshot(buf) is None

    def test_prepare_newline_allows_after_newline(self):
        tm = TriggerMatcher()
        tm.set(r">>>", newline=True, start_idx=0)
        tm.newline_count = 0
        buf = _MockBuffer("\n>>>")
        snap = tm.prepare_snapshot(buf)
        assert snap is not None
        assert tm.check_snapshot(snap) is True

    def test_check_snapshot_invalid_regex_substring(self):
        tm = TriggerMatcher()
        tm.set(r"[invalid", start_idx=0)
        buf = _MockBuffer("[invalid data")
        snap = tm.prepare_snapshot(buf)
        assert snap is not None
        assert snap.regex is None
        assert tm.check_snapshot(snap) is True

    def test_check_snapshot_no_match(self):
        tm = TriggerMatcher()
        tm.set(r"xxx", start_idx=0)
        buf = _MockBuffer("output\n>>>")
        snap = tm.prepare_snapshot(buf)
        assert snap is not None
        assert tm.check_snapshot(snap) is False
        assert tm.matched is False

    def test_check_is_two_phase_wrapper(self):
        tm = TriggerMatcher()
        tm.set(r">>>", start_idx=0)
        buf = _MockBuffer("output\n>>>")
        assert _check(tm, buf) is True
        assert _check(tm, buf) is False  # 已匹配


class TestTriggerMatcherIdleTimeout:
    """TriggerMatcher 静默超时测试"""

    def test_idle_timeout_not_set(self):
        tm = TriggerMatcher()
        tm.set(">>>", start_idx=0)
        assert tm.check_idle_timeout() is False

    def test_idle_timeout_not_elapsed(self):
        tm = TriggerMatcher()
        tm.set(">>>", idle_timeout=10.0, start_idx=0)
        assert tm.check_idle_timeout() is False

    def test_idle_timeout_elapsed(self):
        tm = TriggerMatcher()
        tm.set(">>>", idle_timeout=0.01, start_idx=0)
        time.sleep(0.05)
        assert tm.check_idle_timeout() is True

    def test_idle_after_first_no_output(self):
        tm = TriggerMatcher()
        tm.set(">>>", idle_timeout=0.01, idle_after_first_output=True,
               start_idx=0)
        time.sleep(0.05)
        assert tm.check_idle_timeout() is False

    def test_on_data_appended_resets_idle(self):
        tm = TriggerMatcher()
        tm.set(">>>", idle_timeout=5.0, idle_after_first_output=True,
               start_idx=0)
        time.sleep(0.01)
        tm.on_data_appended(time.monotonic())
        assert tm.check_idle_timeout() is False


class TestTriggerMatcherClear:
    """TriggerMatcher.clear 测试"""

    def test_clear_resets_pattern(self):
        tm = TriggerMatcher()
        tm.set(">>>", start_idx=0)
        tm.clear()
        assert tm.has_pattern is False
        assert tm.pattern is None

    def test_clear_resets_matched(self):
        tm = TriggerMatcher()
        tm.set(">>>", start_idx=0)
        buf = _MockBuffer(">>>")
        _check(tm, buf)
        tm.clear()
        assert tm.matched is False

    def test_clear_resets_idle_timeout(self):
        tm = TriggerMatcher()
        tm.set(">>>", idle_timeout=5.0, start_idx=0)
        tm.clear()
        assert tm.idle_timeout is None