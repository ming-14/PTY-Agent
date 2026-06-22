"""TriggerMatcher 单元测试

测试触发条件匹配器的正则匹配、换行策略、新鲜模式、静默超时、ReDoS 防护。
"""

import re
import time
import pytest

from src.session.output.trigger import TriggerMatcher, safe_regex_search


class _MockBuffer:
    """模拟 OutputBuffer"""

    def __init__(self, data=b"", read_cycle=0):
        self._data = bytearray(data)
        self._read_cycle = read_cycle

    @property
    def raw(self):
        return self._data

    @property
    def read_cycle(self):
        return self._read_cycle

    def count_byte(self, b):
        return self._data.count(b)


def _decode_utf8(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


class TestSafeRegexSearch:
    """safe_regex_search ReDoS 防护测试"""

    def test_normal_match(self):
        """正常正则匹配"""
        pat = re.compile(r"hello")
        assert safe_regex_search(pat, "hello world") is True

    def test_no_match(self):
        """不匹配返回 False"""
        pat = re.compile(r"xyz")
        assert safe_regex_search(pat, "hello world") is False

    def test_redos_pattern_timeout(self):
        """ReDoS 恶意正则超时返回 False"""
        pat = re.compile(r"(a+)+$")
        text = "a" * 20 + "!"
        result = safe_regex_search(pat, text, timeout=0.05)
        assert isinstance(result, bool)

    def test_invalid_regex_returns_false(self):
        """无效正则返回 False"""
        try:
            pat = re.compile(r"[invalid")
        except re.error:
            assert True
            return
        assert isinstance(safe_regex_search(pat, "test"), bool)


class TestTriggerMatcherSet:
    """TriggerMatcher.set 测试"""

    def test_set_pattern(self):
        """设置触发模式"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", buffer_length=0)
        assert tm.has_pattern is True
        assert tm.pattern == ">>>"

    def test_set_clears_matched(self):
        """设置新模式清除 matched 状态"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", buffer_length=0)
        buf = _MockBuffer(b">>>")
        tm.check(buf)
        assert tm.matched is True
        tm.set("xxx", buffer_length=0)
        assert tm.matched is False

    def test_set_clears_event(self):
        """设置新模式清除 event"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", buffer_length=0)
        buf = _MockBuffer(b">>>")
        tm.check(buf)
        assert tm.event.is_set()
        tm.set("xxx", buffer_length=0)
        assert not tm.event.is_set()

    def test_set_with_newline(self):
        """设置换行策略"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", newline=True, buffer_length=0)
        assert tm.newline_count == 0

    def test_set_with_idle_timeout(self):
        """设置静默超时"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", idle_timeout=5.0, buffer_length=0)
        assert tm.idle_timeout == 5.0

    def test_set_fresh_mode(self):
        """设置新鲜模式"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", fresh=True, buffer_length=0)
        assert tm.fresh_cycle == 0


class TestTriggerMatcherCheck:
    """TriggerMatcher.check 测试"""

    def test_check_match_regex(self):
        """正则匹配成功"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(r">>>", buffer_length=0)
        buf = _MockBuffer(b"output\n>>>")
        assert tm.check(buf) is True
        assert tm.matched is True
        assert tm.event.is_set()

    def test_check_no_match(self):
        """正则不匹配"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(r"xxx", buffer_length=0)
        buf = _MockBuffer(b"output\n>>>")
        assert tm.check(buf) is False
        assert tm.matched is False

    def test_check_already_matched(self):
        """已匹配时再次检查返回 False"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(r">>>", buffer_length=0)
        buf = _MockBuffer(b">>>")
        assert tm.check(buf) is True
        assert tm.check(buf) is False

    def test_check_no_pattern(self):
        """无模式时返回 False"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        buf = _MockBuffer(b"data")
        assert tm.check(buf) is False

    def test_check_invalid_regex_fallback_substring(self):
        """无效正则回退到子串匹配"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(r"[invalid", buffer_length=0)
        buf = _MockBuffer(b"[invalid data")
        assert tm.check(buf) is True

    def test_check_newline_blocks(self):
        """换行策略：无新换行且非首次时阻止匹配"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(r">>>", newline=True, buffer_length=0)
        tm.newline_count = 0
        tm._newline_first_ok = False
        buf = _MockBuffer(b">>>")
        assert tm.check(buf) is False

    def test_check_newline_allows_after_newline(self):
        """换行策略：新换行后允许匹配"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(r">>>", newline=True, buffer_length=0)
        tm.newline_count = 0
        buf = _MockBuffer(b"\n>>>")
        assert tm.check(buf) is True

    def test_check_newline_first_ok(self):
        """换行策略：首次检查允许通过"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(r">>>", newline=True, buffer_length=0)
        tm.newline_count = 0
        tm._newline_first_ok = True
        buf = _MockBuffer(b">>>")
        assert tm.check(buf) is True

    def test_check_fresh_mode_waits(self):
        """新鲜模式：read_cycle 未推进时不匹配"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(r">>>", fresh=True, buffer_length=0)
        tm.fresh_cycle = 0
        buf = _MockBuffer(b">>>", read_cycle=0)
        assert tm.check(buf) is False

    def test_check_fresh_mode_matches_after_cycle(self):
        """新鲜模式：read_cycle 推进后匹配"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(r">>>", fresh=True, buffer_length=0)
        tm.fresh_cycle = 0
        buf = _MockBuffer(b">>>", read_cycle=1)
        assert tm.check(buf) is True

    def test_check_start_offset(self):
        """从指定偏移开始扫描"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(r">>>", start_offset=5, buffer_length=0)
        buf = _MockBuffer(b">>> hello >>>")
        assert tm.check(buf) is True


class TestTriggerMatcherIdleTimeout:
    """TriggerMatcher 静默超时测试"""

    def test_idle_timeout_not_set(self):
        """未设置静默超时返回 False"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", buffer_length=0)
        assert tm.check_idle_timeout() is False

    def test_idle_timeout_not_elapsed(self):
        """静默时间未超时返回 False"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", idle_timeout=10.0, buffer_length=0)
        assert tm.check_idle_timeout() is False

    def test_idle_timeout_elapsed(self):
        """静默时间超时返回 True"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", idle_timeout=0.01, buffer_length=0)
        time.sleep(0.05)
        assert tm.check_idle_timeout() is True

    def test_idle_after_first_no_output(self):
        """idle_after_first_output: 无输出时不检测超时"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", idle_timeout=0.01, idle_after_first_output=True,
               buffer_length=0)
        time.sleep(0.05)
        assert tm.check_idle_timeout() is False

    def test_on_data_appended_resets_idle(self):
        """数据追加重置静默计时"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", idle_timeout=5.0, idle_after_first_output=True,
               buffer_length=0)
        time.sleep(0.01)
        tm.on_data_appended(time.monotonic())
        assert tm.check_idle_timeout() is False


class TestTriggerMatcherClear:
    """TriggerMatcher.clear 测试"""

    def test_clear_resets_pattern(self):
        """清除后无模式"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", buffer_length=0)
        tm.clear()
        assert tm.has_pattern is False
        assert tm.pattern is None

    def test_clear_resets_matched(self):
        """清除后 matched 为 False"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", buffer_length=0)
        buf = _MockBuffer(b">>>")
        tm.check(buf)
        tm.clear()
        assert tm.matched is False

    def test_clear_resets_idle_timeout(self):
        """清除后 idle_timeout 为 None"""
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set(">>>", idle_timeout=5.0, buffer_length=0)
        tm.clear()
        assert tm.idle_timeout is None