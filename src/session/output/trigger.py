"""触发条件匹配器 — 正则匹配 + 输出静默超时检测

职责独立于 Session，不持有 PTY 或缓冲区引用，通过回调与
OutputBuffer / Session 协作。

关键设计:
- 匹配逻辑在持锁路径（OutputBuffer.lock）中执行，通过传入的
  OutputBuffer 引用直接读取原始字节。
- 解码依赖外部的 decode_func 回调（Session._decode_only），
  避免引入编码探测的循环依赖。
- ReDoS 防护: safe_regex_search 在独立 daemon 线程中执行，
  超时自动降级返回 False。
"""

import re
import time
import logging
import threading
import concurrent.futures
from typing import Callable, Optional

from ...config import MAX_TRIGGER_SCAN

_logger = logging.getLogger("pty-session")

# ReDoS 防护：正则搜索超时保护
_RE_SEARCH_TIMEOUT = 2.0

# 共享线程池（最多 4 个 worker，线程名前缀 safe-regex）
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="safe-regex",
)


def safe_regex_search(pattern: re.Pattern, text: str,
                      timeout: float = _RE_SEARCH_TIMEOUT) -> bool:
    """在共享线程池中执行正则搜索，超时安全降级返回 False

    使用 ThreadPoolExecutor 复用线程，避免每次创建/销毁开销。
    超时后调用 future.cancel() 提示中断（实际正则运行无法立即停止，
    但线程数被 max_workers 限制，不会无限增长）。
    """
    future = _EXECUTOR.submit(pattern.search, text)
    try:
        return future.result(timeout=timeout) is not None
    except concurrent.futures.TimeoutError:
        _logger.warning("正则搜索超时: pattern=%r, text_len=%d",
                        pattern.pattern[:200], len(text))
        future.cancel()
        return False
    except re.error:
        return False


class TriggerMatcher:
    """触发条件匹配器

    管理一组触发条件（正则/子串匹配 + 换行策略 + 新鲜模式 + 静默超时）。
    不直接持有 IO 资源，通过回调与 OutputBuffer 协作。
    """

    def __init__(self, decode_func: Callable[[bytes], str]):
        """
        Args:
            decode_func: 解码回调，接收 bytes 返回 str。
                         通常为 Session._decode_only。
        """
        self._decode_func = decode_func

        # 触发条件状态
        self._pattern: Optional[str] = None
        self._regex: Optional[re.Pattern] = None  # 预编译正则
        self._matched = False
        self._event = threading.Event()
        self._start_offset = 0
        self._on_newline = False
        self._newline_count = 0
        self._newline_first_ok = False
        self._fresh = False
        self._fresh_cycle = 0

        # 输出静默超时触发条件
        self._idle_timeout: Optional[float] = None
        self._idle_after_first = False
        self._idle_last_activity = 0.0
        self._idle_had_output = False

    # ── 公开接口 ──

    def set(self, pattern: str, newline: bool = False, fresh: bool = False,
            start_offset: Optional[int] = None,
            idle_timeout: Optional[float] = None,
            idle_after_first_output: bool = False,
            buffer_length: int = 0):
        """设置触发条件

        Args:
            pattern:              正则表达式模式。
            newline:              仅在换行后才检查触发条件。
            fresh:                新鲜模式 — 跳过即时匹配等待新数据。
            start_offset:         扫描起始偏移。None 表示从末尾开始。
            idle_timeout:         输出静默超时秒数。
            idle_after_first_output: 是否在首次输出后才开始检测。
            buffer_length:        当前缓冲区长度（用于计算 start_offset）。
        """
        self._pattern = pattern
        try:
            self._regex = re.compile(pattern)
        except re.error:
            self._regex = None
        self._matched = False
        self._event.clear()
        self._start_offset = (start_offset if start_offset is not None
                              else buffer_length)
        self._on_newline = newline

        # 初始化静默超时
        self._idle_timeout = idle_timeout
        self._idle_after_first = idle_after_first_output
        now = time.monotonic()
        if idle_timeout is not None:
            if idle_after_first_output:
                self._idle_had_output = False
                self._idle_last_activity = now
            else:
                self._idle_had_output = True
                self._idle_last_activity = now

        _logger.info(
            "TriggerMatcher.set: pattern=%r newline=%s fresh=%s "
            "offset=%d idle_timeout=%s idle_after_first=%s",
            pattern, newline, fresh, self._start_offset,
            idle_timeout, idle_after_first_output)

        if fresh:
            self._fresh = True
            self._fresh_cycle = 0  # 由调用者设置实际值
            return

        self._newline_first_ok = newline
        self._newline_count = 0  # 由调用者在持锁后更新

    def on_data_appended(self, now_monotonic: float):
        """通知有新数据追加（更新静默超时计时）

        Args:
            now_monotonic: time.monotonic() 当前值。
        """
        if self._idle_timeout is not None:
            self._idle_last_activity = now_monotonic
            if not self._idle_had_output:
                self._idle_had_output = True
                _logger.debug("静默超时检测: 首次输出到达, 开始计时")

    def check(self, output_buffer) -> bool:
        """检查触发条件是否匹配（**需在持锁状态下调用**）

        需在 OutputBuffer.lock 已获取的线程上下文中调用。

        Args:
            output_buffer: OutputBuffer 实例（持锁状态下）。

        Returns:
            True 表示匹配成功并设置了 _event。
        """
        if not self._pattern or self._matched:
            return False

        # 新鲜模式：等待读周期推进后才开始检查
        if self._fresh:
            if output_buffer.read_cycle <= self._fresh_cycle:
                return False
            self._fresh = False

        if self._on_newline:
            cur = output_buffer.raw.count(b"\n")
            if cur > self._newline_count:
                self._newline_count = cur
            elif self._newline_first_ok:
                self._newline_first_ok = False
            else:
                return False

        # 从 offset 开始扫描，限制最大范围
        start = min(self._start_offset, len(output_buffer.raw))
        end = min(start + MAX_TRIGGER_SCAN, len(output_buffer.raw))
        raw = bytes(output_buffer.raw[start:end])
        text = self._decode_func(raw)

        if self._regex:
            if safe_regex_search(self._regex, text):
                _logger.info("TriggerMatcher.check: MATCHED pattern=%r",
                             self._pattern)
                self._matched = True
                self._event.set()
                return True
        else:
            # 正则无效时回退到子串匹配
            if self._pattern in text:
                _logger.info("TriggerMatcher.check: substring MATCHED "
                             "pattern=%r", self._pattern)
                self._matched = True
                self._event.set()
                return True
        return False

    def check_idle_timeout(self) -> bool:
        """检查输出静默是否超时

        Returns:
            True 表示已超时。
        """
        if self._idle_timeout is None:
            return False
        if not self._idle_had_output and self._idle_after_first:
            return False
        elapsed = time.monotonic() - self._idle_last_activity
        return elapsed >= self._idle_timeout

    def clear(self):
        """清除所有触发条件"""
        _logger.info("TriggerMatcher.clear: pattern=%r matched=%s",
                     self._pattern, self._matched)
        self._pattern = None
        self._regex = None
        self._matched = False
        self._fresh = False
        self._event.clear()
        self._idle_timeout = None
        self._idle_after_first = False
        self._idle_had_output = False
        self._idle_last_activity = 0.0

    # ── 属性 ──

    @property
    def has_pattern(self) -> bool:
        return self._pattern is not None

    @property
    def matched(self) -> bool:
        return self._matched

    @property
    def event(self) -> threading.Event:
        return self._event

    @property
    def pattern(self) -> Optional[str]:
        return self._pattern

    @property
    def idle_timeout(self) -> Optional[float]:
        return self._idle_timeout

    @property
    def newline_count(self) -> int:
        return self._newline_count

    @newline_count.setter
    def newline_count(self, value: int):
        self._newline_count = value

    @property
    def fresh_cycle(self) -> int:
        return self._fresh_cycle

    @fresh_cycle.setter
    def fresh_cycle(self, value: int):
        self._fresh_cycle = value
