"""输出缓冲区 — 线程安全的 PTY 输出数据缓冲

管理字节缓冲区的追加、裁剪、查询，并提供锁机制供协调者
（Session._reader_loop）与 TriggerMatcher 在原子上下文中的协作。
"""

import logging
import threading
from contextlib import contextmanager
from typing import Optional

from ...config import MAX_OUTPUT_BUFFER

_logger = logging.getLogger("pty-session")


class OutputBuffer:
    """线程安全的输出缓冲区

    封装原始 bytearray，所有公开的读/写操作均通过内部锁保护。
    同时暴露 lock 与 raw 属性，供协调者在持锁上下文中直接访问
    原始缓冲区（例如与 TriggerMatcher 配合时避免二次加锁）。
    """

    def __init__(self, max_size: int = MAX_OUTPUT_BUFFER):
        self._buffer = bytearray()
        self._lock = threading.RLock()  # RLock 允许同一线程重入（用于 reader_loop 持锁时调用 append）
        self._read_cycle = 0
        self._max_size = max_size
        self._first_output_event = threading.Event()

    # ── 线程安全方法 ──

    def append(self, data: bytes) -> bool:
        """追加数据到缓冲区尾部

        当缓冲区超过最大容量时，丢弃前半部分数据并跳过本次追加。

        Args:
            data: 待追加的字节数据。

        Returns:
            True  成功追加；
            False 缓冲区满已裁剪，本次数据被丢弃。
        """
        with self._lock:
            room = self._max_size - len(self._buffer)
            if room <= 0:
                drop = len(self._buffer) // 2
                del self._buffer[:drop]
                self._read_cycle += 1
                self._first_output_event.set()
                _logger.warning("OutputBuffer: overflow, trimmed %d bytes", drop)
                return False
            if len(data) > room:
                data = data[:room]
                _logger.debug("OutputBuffer: truncated to %d bytes (room=%d)", len(data), room)
            self._buffer.extend(data)
            self._read_cycle += 1
            self._first_output_event.set()
            if self._read_cycle % 100 == 0:
                _logger.debug("OutputBuffer: size=%d cycle=%d", len(self._buffer), self._read_cycle)
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
            if start < 0:
                start = 0
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
