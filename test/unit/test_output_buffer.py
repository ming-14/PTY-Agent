"""OutputBuffer 单元测试

测试线程安全输出缓冲区的追加、裁剪、查询、线程安全等。
"""

import threading
import pytest

from src.session.output.buffer import OutputBuffer


class TestOutputBufferAppend:
    """OutputBuffer.append 测试"""

    def test_append_returns_true(self):
        """正常追加返回 True"""
        buf = OutputBuffer(max_size=1024)
        assert buf.append(b"hello") is True

    def test_append_data_stored(self):
        """追加的数据可通过 get_slice 读取"""
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello")
        assert buf.get_slice() == b"hello"

    def test_append_multiple(self):
        """多次追加数据拼接"""
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello")
        buf.append(b" ")
        buf.append(b"world")
        assert buf.get_slice() == b"hello world"

    def test_append_empty_bytes(self):
        """追加空 bytes 返回 True"""
        buf = OutputBuffer(max_size=1024)
        assert buf.append(b"") is True

    def test_append_increments_read_cycle(self):
        """每次追加递增 read_cycle"""
        buf = OutputBuffer(max_size=1024)
        assert buf.read_cycle == 0
        buf.append(b"a")
        assert buf.read_cycle == 1
        buf.append(b"b")
        assert buf.read_cycle == 2

    def test_append_sets_first_output_event(self):
        """追加数据后 first_output_event 被设置"""
        buf = OutputBuffer(max_size=1024)
        assert not buf.first_output_event.is_set()
        buf.append(b"data")
        assert buf.first_output_event.is_set()


class TestOutputBufferOverflow:
    """OutputBuffer 溢出裁剪测试"""

    def test_overflow_returns_false(self):
        """缓冲区满时追加返回 False"""
        buf = OutputBuffer(max_size=16)
        buf.append(b"x" * 16)
        assert buf.append(b"y") is False

    def test_overflow_trims_front(self):
        """缓冲区满时裁剪前半部分"""
        buf = OutputBuffer(max_size=16)
        buf.append(b"a" * 16)
        buf.append(b"b")
        data = buf.get_slice()
        assert len(data) < 16
        assert b"b" not in data

    def test_append_truncated_to_room(self):
        """数据超过剩余空间时截断"""
        buf = OutputBuffer(max_size=10)
        buf.append(b"12345")
        result = buf.append(b"67890extra")
        assert result is True
        data = buf.get_slice()
        assert len(data) == 10

    def test_overflow_increments_read_cycle(self):
        """溢出裁剪也递增 read_cycle"""
        buf = OutputBuffer(max_size=8)
        buf.append(b"x" * 8)
        cycle_before = buf.read_cycle
        buf.append(b"y")
        assert buf.read_cycle > cycle_before


class TestOutputBufferGetSlice:
    """OutputBuffer.get_slice 测试"""

    def test_get_slice_default(self):
        """默认获取全部数据"""
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello world")
        assert buf.get_slice() == b"hello world"

    def test_get_slice_with_start(self):
        """指定起始偏移"""
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello world")
        assert buf.get_slice(start=6) == b"world"

    def test_get_slice_with_end(self):
        """指定结束偏移"""
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello world")
        assert buf.get_slice(start=0, end=5) == b"hello"

    def test_get_slice_start_beyond_length(self):
        """起始偏移超出长度返回空"""
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello")
        assert buf.get_slice(start=100) == b""

    def test_get_slice_negative_start(self):
        """负数起始偏移被修正为 0"""
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello")
        assert buf.get_slice(start=-5) == b"hello"

    def test_get_slice_empty_buffer(self):
        """空缓冲区返回空 bytes"""
        buf = OutputBuffer(max_size=1024)
        assert buf.get_slice() == b""


class TestOutputBufferProperties:
    """OutputBuffer 属性测试"""

    def test_length_empty(self):
        """空缓冲区长度为 0"""
        buf = OutputBuffer(max_size=1024)
        assert buf.length == 0

    def test_length_after_append(self):
        """追加后长度增加"""
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello")
        assert buf.length == 5

    def test_count_byte(self):
        """统计指定字节出现次数"""
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello\nworld\n")
        assert buf.count_byte(ord("\n")) == 2
        assert buf.count_byte(ord("l")) == 3

    def test_raw_returns_bytearray(self):
        """raw 属性返回 bytearray"""
        buf = OutputBuffer(max_size=1024)
        assert isinstance(buf.raw, bytearray)

    def test_lock_returns_rlock(self):
        """lock 属性返回 RLock"""
        buf = OutputBuffer(max_size=1024)
        assert isinstance(buf.lock, type(threading.RLock()))


class TestOutputBufferConcurrency:
    """OutputBuffer 并发安全测试"""

    def test_concurrent_appends(self):
        """多线程并发追加数据不丢失"""
        buf = OutputBuffer(max_size=102400)
        n_threads = 5
        n_appends = 100
        data = b"x" * 10

        def worker():
            for _ in range(n_appends):
                buf.append(data)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert buf.length > 0