"""session/output/trigger.py 单元测试"""

import time
import pytest

from src.output.trigger import TriggerMatcher, safe_regex_search


class _MockOutputBuffer:
    def __init__(self, data=b""):
        self._data = bytearray(data)
        self._read_cycle = 1
        self._trim_gen = 0

    @property
    def raw(self):
        return self._data

    @property
    def read_cycle(self):
        return self._read_cycle

    @property
    def trim_gen(self):
        """缓冲头部裁剪代次（与 OutputBuffer.trim_gen 语义一致）"""
        return self._trim_gen

    def count_byte(self, b):
        return self._data.count(b)


def _decode_utf8(data):
    """(文本, 消费字节数) 契约的解码回调（消费量按严格解码成功的完整前缀）"""
    return data.decode("utf-8", errors="replace"), len(data)


def _decode_utf8_len(data):
    """与 EncodingDetector.decode_only_len 等价：截尾后返回消费字节数"""
    from src.encoding.codec import decode_strip_tail_len

    return decode_strip_tail_len(data, "utf-8")


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


class TestTriggerMatcherRollingCache:
    """滚动解码缓存：跨块匹配 / 裁剪失效 / 双缓冲切换"""

    def test_match_in_second_chunk(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set("world", buffer_length=0)
        buf = _MockOutputBuffer()
        assert tm.check(buf) is False
        buf._data.extend(b"hello ")
        assert tm.check(buf) is False
        buf._data.extend(b"world")
        assert tm.check(buf) is True

    def test_match_across_chunk_boundary(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set(r"hel\s*lo", buffer_length=0)
        buf = _MockOutputBuffer()
        buf._data.extend(b"hel")
        assert tm.check(buf) is False
        buf._data.extend(b"\nlo")
        assert tm.check(buf) is True

    def test_substring_match_across_chunk_boundary(self):
        tm = TriggerMatcher(_decode_utf8)
        # 非法正则 → 子串匹配路径
        tm.set("[invalid", buffer_length=0)
        buf = _MockOutputBuffer()
        buf._data.extend(b"xx [inva")
        assert tm.check(buf) is False
        buf._data.extend(b"lid regex")
        assert tm.check(buf) is True

    def test_cache_invalidated_on_buffer_trim(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set(">>>", buffer_length=0)
        buf = _MockOutputBuffer(b"old content")
        assert tm.check(buf) is False
        # 缓冲被头部裁剪（trim_gen 递增）→ 缓存重建，从新窗口起点重新扫描
        buf._data = bytearray(b"tail >>>")
        buf._trim_gen += 1
        assert tm.check(buf) is True

    def test_cache_switch_buffer(self):
        tm = TriggerMatcher(_decode_utf8)
        tm.set("match", buffer_length=0)
        buf_a = _MockOutputBuffer(b"aaa")
        buf_b = _MockOutputBuffer(b"bbb")
        assert tm.check(buf_a) is False
        assert tm.check(buf_b) is False
        buf_b._data.extend(b" match")
        assert tm.check(buf_b) is True

    def test_start_offset_window_capped(self):
        """等待窗口上限 MAX_TRIGGER_SCAN 内仍只增量解码新增部分"""
        tm = TriggerMatcher(_decode_utf8)
        tm.set("done", buffer_length=0)
        buf = _MockOutputBuffer()
        big = b"x" * (1 << 20)  # 超过 1MB 等待窗口
        buf._data.extend(big)
        assert tm.check(buf) is False
        # 窗口已封顶，追加内容超出窗口不参与扫描
        buf._data.extend(b"done")
        assert tm.check(buf) is False

    def test_multibyte_split_across_chunks(self):
        """多字节字符跨块拆分：残缺尾部与下块合并解码补全，匹配不丢字"""
        tm = TriggerMatcher(_decode_utf8_len)
        tm.set("成完", buffer_length=0)
        buf = _MockOutputBuffer()
        buf._data.extend("已".encode())  # 完整字符
        assert tm.check(buf) is False
        buf._data.extend("成".encode()[:2])  # '成' 前两字节（不完整）
        assert tm.check(buf) is False
        buf._data.extend(b"\x90")  # '成' 末字节
        assert tm.check(buf) is False
        buf._data.extend("完".encode())  # 补全后 '成完' 命中
        assert tm.check(buf) is True
        assert tm._scan_text == "已成完"


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
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set_snapshot_trigger(pattern=r"prompt>")
        assert tm.check_snapshot("hello prompt> world") is True
        assert tm.matched is True

    def test_check_snapshot_no_match(self):
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set_snapshot_trigger(pattern=r"prompt>")
        assert tm.check_snapshot("hello world") is False

    def test_check_snapshot_substring_fallback(self):
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set_snapshot_trigger(pattern="[invalid regex")
        assert tm.check_snapshot("text with [invalid regex inside") is True

    def test_check_snapshot_no_pattern(self):
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set_snapshot_trigger(idle_timeout=1.0)
        assert tm.check_snapshot("anything") is False

    def test_snapshot_idle_timeout_not_elapsed(self):
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set_snapshot_trigger(idle_timeout=5.0)
        tm.notify_snapshot_changed(time.monotonic())
        assert tm.check_idle_timeout() is False

    def test_snapshot_idle_timeout_elapsed(self):
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set_snapshot_trigger(idle_timeout=0.01)
        tm.notify_snapshot_changed(time.monotonic())
        time.sleep(0.05)
        assert tm.check_idle_timeout() is True

    def test_snapshot_idle_after_first_output(self):
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set_snapshot_trigger(idle_timeout=0.01, idle_after_first_output=True)
        assert tm.check_idle_timeout() is False
        tm.notify_snapshot_changed(time.monotonic())
        time.sleep(0.05)
        assert tm.check_idle_timeout() is True

    def test_snapshot_trigger_and_idle_combined(self):
        tm = TriggerMatcher(decode_func=_decode_utf8)
        tm.set_snapshot_trigger(pattern=r"done", idle_timeout=5.0)
        assert tm.check_snapshot("working...") is False
        assert tm.check_snapshot("all done!") is True
        assert tm.matched is True
