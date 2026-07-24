"""session/output/buffer.py 单元测试"""

import threading
import pytest

from src.output.buffer import OutputBuffer


class TestOutputBufferAppend:
    def test_append_returns_true(self):
        buf = OutputBuffer(max_size=1024)
        assert buf.append(b"hello") is True

    def test_append_data_stored(self):
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello")
        assert buf.get_slice() == b"hello"

    def test_append_multiple(self):
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello")
        buf.append(b" ")
        buf.append(b"world")
        assert buf.get_slice() == b"hello world"

    def test_append_empty_bytes(self):
        buf = OutputBuffer(max_size=1024)
        assert buf.append(b"") is True

    def test_append_increments_read_cycle(self):
        buf = OutputBuffer(max_size=1024)
        assert buf.read_cycle == 0
        buf.append(b"a")
        assert buf.read_cycle == 1
        buf.append(b"b")
        assert buf.read_cycle == 2

    def test_append_sets_first_output_event(self):
        buf = OutputBuffer(max_size=1024)
        assert not buf.first_output_event.is_set()
        buf.append(b"data")
        assert buf.first_output_event.is_set()


class TestOutputBufferOverflow:
    def test_overflow_returns_true_and_trims(self):
        buf = OutputBuffer(max_size=16)
        buf.append(b"x" * 16)
        assert buf.append(b"y") is True
        assert buf.dropped_bytes > 0

    def test_overflow_trims_front_keeps_new_data(self):
        buf = OutputBuffer(max_size=16)
        buf.append(b"a" * 16)
        buf.append(b"b")
        data = buf.get_slice()
        assert len(data) <= 16
        assert b"b" in data

    def test_append_trims_to_max_size(self):
        buf = OutputBuffer(max_size=10)
        buf.append(b"12345")
        result = buf.append(b"67890extra")
        assert result is True
        data = buf.get_slice()
        assert len(data) == 10


class TestOutputBufferGetSlice:
    def test_get_slice_default(self):
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello world")
        assert buf.get_slice() == b"hello world"

    def test_get_slice_with_start(self):
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello world")
        assert buf.get_slice(start=6) == b"world"

    def test_get_slice_with_end(self):
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello world")
        assert buf.get_slice(start=0, end=5) == b"hello"

    def test_get_slice_start_beyond_length(self):
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello")
        assert buf.get_slice(start=100) == b""

    def test_get_slice_negative_start(self):
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello")
        assert buf.get_slice(start=-5) == b"hello"

    def test_get_slice_empty_buffer(self):
        buf = OutputBuffer(max_size=1024)
        assert buf.get_slice() == b""


class TestOutputBufferProperties:
    def test_length_empty(self):
        buf = OutputBuffer(max_size=1024)
        assert buf.length == 0

    def test_length_after_append(self):
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello")
        assert buf.length == 5

    def test_count_byte(self):
        buf = OutputBuffer(max_size=1024)
        buf.append(b"hello\nworld\n")
        assert buf.count_byte(ord("\n")) == 2
        assert buf.count_byte(ord("l")) == 3

    def test_raw_returns_bytearray(self):
        buf = OutputBuffer(max_size=1024)
        assert isinstance(buf.raw, bytearray)

    def test_lock_returns_rlock(self):
        buf = OutputBuffer(max_size=1024)
        assert isinstance(buf.lock, type(threading.RLock()))


class TestOutputBufferConcurrency:
    def test_concurrent_appends(self):
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
