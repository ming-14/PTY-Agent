"""输出缓冲区 — 线程安全的 PTY 输出数据缓冲

管理字节缓冲区的追加、裁剪、查询，并提供锁机制供协调者
（Session._reader_loop）与 TriggerMatcher 在原子上下文中的协作。
"""

import threading
from typing import Optional

from ..config.daemon import MAX_OUTPUT_BUFFER
from ..logging import get_logger

_logger = get_logger("pty-session")

# 溢出裁剪 headroom 除数：缓冲超过 max_size + max_size//_TRIM_HEADROOM_DIVISOR
# 才裁剪回 max_size。将持锁 del 的 O(n) memmove 从"每块一次"摊还为
# "每 headroom 一次"（大缓冲下 memmove 约 10-30ms，每块触发不可接受）。
_TRIM_HEADROOM_DIVISOR = 4


class OutputBuffer:
    """线程安全的输出缓冲区

    封装原始 bytearray，所有公开的读/写操作均通过内部锁保护。
    同时暴露 lock 与 raw 属性，供协调者在持锁上下文中直接访问
    原始缓冲区（例如与 TriggerMatcher 配合时避免二次加锁）。

    缓冲上限为软上限：裁剪按固定块批量执行（见 _TRIM_HEADROOM_DIVISOR），
    实际容量瞬时可达 max_size + headroom，裁剪后回落到 max_size。
    """

    def __init__(self, max_size: int = MAX_OUTPUT_BUFFER):
        self._buffer = bytearray()
        self._lock = threading.RLock()
        self._read_cycle = 0
        self._max_size = max_size
        self._first_output_event = threading.Event()
        self._dropped_bytes = 0
        # 头部裁剪代次：溢出裁剪时递增，供 TriggerMatcher 滚动缓存失效判定
        self._trim_gen = 0
        # 头部已裁剪的绝对流偏移：裁剪时递增，供"绝对流偏移"读取映射/丢失检测。
        # 使增量游标在裁剪后仍单调有效（否则绝对字节偏移随头部 del 左移而失效）。
        self._trim_base = 0

    @property
    def dropped_bytes(self) -> int:
        with self._lock:
            return self._dropped_bytes

    @property
    def trim_gen(self) -> int:
        """头部裁剪代次（每次溢出裁剪递增；触发滚动缓存据此失效）"""
        with self._lock:
            return self._trim_gen

    @property
    def trim_base(self) -> int:
        """头部已裁剪的绝对流偏移（从流开始计的保留数据起点）

        单调递增（写入永不消失，仅裁剪最旧字节）。绝对流偏移 = trim_base + 物理下标。
        """
        with self._lock:
            return self._trim_base

    @property
    def stream_end(self) -> int:
        """当前流末尾的绝对流偏移（单调递增，不受头部裁剪影响）"""
        with self._lock:
            return self._trim_base + len(self._buffer)

    def read_stream(self, stream_start: int) -> tuple:
        """按绝对流偏移读取保留输出（增量/查询的基础读取）

        Args:
            stream_start: 起始绝对流偏移（负值按 0 处理）。

        Returns:
            (bytes, actual_start, dropped_before)：
            - bytes: 自 actual_start 起的保留数据；actual_start 之前的数据已被裁剪。
            - actual_start: 实际生效的起始流偏移（= max(stream_start, trim_base)）。
            - dropped_before: stream_start 落在 trim_base 之前（请求起点数据已丢）为 True。
        """
        with self._lock:
            start = max(stream_start, 0)
            actual_start = start if start >= self._trim_base else self._trim_base
            dropped_before = start < self._trim_base
            rel = actual_start - self._trim_base
            if rel >= len(self._buffer):
                return b"", actual_start, dropped_before
            return bytes(memoryview(self._buffer)[rel:]), actual_start, dropped_before

    def append(self, data: bytes) -> bool:
        """追加数据到缓冲区尾部

        当缓冲区超过最大容量时，先追加新数据再从头部裁剪，
        确保新数据不丢失，仅丢弃最旧的历史数据。

        Args:
            data: 待追加的字节数据。

        Returns:
            True  成功追加（可能伴随旧数据裁剪）。
        """
        with self._lock:
            self._buffer.extend(data)
            if len(self._buffer) > self._max_size + self._max_size // _TRIM_HEADROOM_DIVISOR:
                drop = len(self._buffer) - self._max_size
                del self._buffer[:drop]
                self._dropped_bytes += drop
                self._trim_gen += 1
                self._trim_base += drop
                self._read_cycle += 1
                self._first_output_event.set()
                _logger.warning(
                    "OutputBuffer: overflow, trimmed %d bytes (total dropped: %d)",
                    drop,
                    self._dropped_bytes,
                )
            else:
                self._read_cycle += 1
                self._first_output_event.set()
            if self._read_cycle % 100 == 0:
                _logger.debug(
                    "OutputBuffer: size=%d cycle=%d",
                    len(self._buffer),
                    self._read_cycle,
                )
            return True

    def get_slice(self, start: int = 0, end: Optional[int] = None) -> bytes:
        """获取缓冲区切片（线程安全）

        Args:
            start: 起始字节偏移。
            end:   结束字节偏移（不含），None 表示到末尾。

        Returns:
            切片对应的 bytes 对象。
        """
        with self._lock:
            if end is None:
                end = len(self._buffer)
            start = max(start, 0)
            if start >= len(self._buffer):
                return b""
            return bytes(memoryview(self._buffer)[start:end])

    @property
    def length(self) -> int:
        """当前缓冲区字节长度"""
        with self._lock:
            return len(self._buffer)

    @property
    def read_cycle(self) -> int:
        """读取周期计数（每次 append 递增）"""
        with self._lock:
            return self._read_cycle

    def get_slice_with_length(self, start: int = 0) -> tuple:
        """原子获取缓冲区切片及当前长度（消除 TOCTOU 竞态）

        Args:
            start: 起始字节偏移。

        Returns:
            (切片 bytes, 当前缓冲区总长度) 元组。
        """
        with self._lock:
            length = len(self._buffer)
            start = max(start, 0)
            if start >= length:
                return b"", length
            return bytes(memoryview(self._buffer)[start:]), length

    def count_byte(self, b: int) -> int:
        """统计指定字节在缓冲区中的出现次数"""
        with self._lock:
            return self._buffer.count(b)

    # ── 协调访问（供 Session._reader_loop 在持锁语境下使用）──

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    @property
    def raw(self) -> bytearray:
        """原始缓冲区引用（**仅在持锁时使用**）"""
        return self._buffer

    @property
    def first_output_event(self) -> threading.Event:
        return self._first_output_event
