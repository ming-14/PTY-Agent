"""输出缓冲区 — 线程安全的文本行级输出缓冲

两种后端模式统一使用文本行语义：
- history：完整行（pty 模式为滚动历史；subprocess 模式为全部输出行）
- visible：当前可见屏幕行（仅 pty 模式使用；subprocess 模式为空）
- tail：当前未完成的行（仅 subprocess 流式输出使用）

游标（cursor）语义：已消费的历史行数。exec/send 的"增量输出"
= history[cursor:] + visible（+ 未完成 tail），CLI 不暴露 offset。

线程安全：所有读写经 RLock 保护；暴露 lock/raw 供协调者在持锁
上下文协作（与 TriggerMatcher 配合）。
"""

import logging
import threading
from typing import List

from ...config import (
    MAX_HISTORY_LINES,
    MAX_OUTPUT_CHARS,
)

_logger = logging.getLogger("pty-session")


class OutputBuffer:
    """线程安全的文本行级输出缓冲区

    管理完整行列表（history + visible）与未完成尾部，提供全量/可见/
    游标增量读取。所有公开的读/写操作均通过内部锁保护。
    """

    def __init__(self, max_lines: int = MAX_HISTORY_LINES,
                 max_chars: int = MAX_OUTPUT_CHARS):
        self._history: List[str] = []   # 完整历史行
        self._visible: List[str] = []   # 可见屏幕行（pty 模式）
        self._tail: str = ""            # 未完成行（subprocess 流）
        self._cursor: int = 0           # 已消费历史行数（mark/reset 显式控制）
        self._lock = threading.RLock()  # RLock 允许同线程重入
        self._read_cycle = 0
        self._max_lines = max_lines
        self._max_chars = max_chars
        self._first_output_event = threading.Event()

    # ════════════════════════════════════════════════════════════
    # 写入（reader 线程 / pipeline 调用）
    # ════════════════════════════════════════════════════════════

    def append_text(self, text: str) -> None:
        """追加流式文本（subprocess 模式）：按 \\n 拆行

        Args:
            text: 解码后的文本块。
        """
        with self._lock:
            if not text:
                return
            parts = text.split("\n")
            # 首段与既有未完成行拼接，成为首行
            parts[0] = self._tail + parts[0]
            if text.endswith("\n"):
                # 全部为完整行（含结尾空行）
                for line in parts:
                    self._append_line(line)
                self._tail = ""
            else:
                # 除末尾段外均为完整行；末尾段为未完成行
                for line in parts[:-1]:
                    self._append_line(line)
                self._tail = parts[-1]
            self._read_cycle += 1
            self._first_output_event.set()
            self._trim()

    def append_history(self, rows: List[str]) -> None:
        """追加滚动历史行（pty 模式）

        Args:
            rows: 新滚出屏幕的完整行列表。
        """
        with self._lock:
            if not rows:
                return
            for line in rows:
                self._append_line(line)
            self._read_cycle += 1
            self._first_output_event.set()
            self._trim()

    def replace_visible(self, rows: List[str]) -> None:
        """替换可见屏幕行（pty 模式，每次渲染后调用）

        Args:
            rows: 屏幕当前可见行（已去除宽度填充）。
        """
        with self._lock:
            if rows == self._visible:
                return
            self._visible = list(rows)
            self._read_cycle += 1
            self._first_output_event.set()

    # ════════════════════════════════════════════════════════════
    # 游标
    # ════════════════════════════════════════════════════════════

    def mark_cursor(self) -> None:
        """将游标定位到当前历史末尾（下一次增量读取起点）"""
        with self._lock:
            self._cursor = len(self._history)

    def reset_cursor(self) -> None:
        """将游标复位到 0（下一次读取=全量）"""
        with self._lock:
            self._cursor = 0

    @property
    def cursor(self) -> int:
        """当前游标（历史行索引）"""
        with self._lock:
            return self._cursor

    # ════════════════════════════════════════════════════════════
    # 读取
    # ════════════════════════════════════════════════════════════

    def get_visible(self) -> str:
        """可见屏幕文本（pty 模式）；subprocess 模式返回完整缓冲文本"""
        with self._lock:
            if self._visible_is_active():
                return "\n".join(self._visible)
            return self._full_text()

    def get_full(self) -> str:
        """全量输出文本（历史 + 可见屏幕；subprocess 含未完成尾部）"""
        with self._lock:
            return self._full_text()

    def get_since_cursor(self) -> str:
        """游标之后的增量文本"""
        with self._lock:
            return self._since_text(self._cursor)

    def get_text_since(self, idx: int, max_chars: int = 0) -> str:
        """从行索引 idx 起取文本（触发扫描用，超长截尾）

        Args:
            idx:       起始行索引（历史内）。
            max_chars: 截断上限，0 表示不截断。

        Returns:
            文本字符串。
        """
        with self._lock:
            text = self._since_text(idx)
            if max_chars and len(text) > max_chars:
                return text[-max_chars:]
            return text

    def get_all_lines(self) -> List[str]:
        """返回全部完整行（含未完成尾部作为最后一行）"""
        with self._lock:
            rows = list(self._history) + list(self._visible)
            if self._tail:
                rows.append(self._tail)
            return rows

    def get_visible_lines(self) -> List[str]:
        """返回可见屏幕行列表（pty 模式）；subprocess 返回全部行"""
        with self._lock:
            if self._visible_is_active():
                return list(self._visible)
            return self.get_all_lines()

    @property
    def line_count(self) -> int:
        """完整行总数（历史 + 可见屏）"""
        with self._lock:
            return len(self._history) + len(self._visible)

    @property
    def read_cycle(self) -> int:
        """读取周期计数（每次内容变化递增）"""
        with self._lock:
            return self._read_cycle

    @property
    def first_output_event(self) -> threading.Event:
        """首个输出事件"""
        return self._first_output_event

    # ── 协调访问（供 Session/TriggerMatcher 在持锁语境下使用）──

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    @property
    def raw(self):
        """仅用于协调名的兼容别名（当前行数据），**仅在持锁时使用**"""
        return {"history": self._history, "visible": self._visible,
                "tail": self._tail}

    # ════════════════════════════════════════════════════════════
    # 内部
    # ════════════════════════════════════════════════════════════

    def _visible_is_active(self) -> bool:
        """subprocess 模式无可见屏幕概念（visible 恒为空）"""
        return bool(self._visible)

    def _append_line(self, line: str) -> None:
        """追加一条完整行到历史（游标不随之移动，仅由 mark/reset 显式控制）"""
        self._history.append(line)

    def _full_text(self) -> str:
        parts = list(self._history) + list(self._visible)
        if self._tail:
            parts.append(self._tail)
        return "\n".join(parts)

    def _since_text(self, idx: int) -> str:
        idx = max(0, min(idx, len(self._history)))
        parts = list(self._history[idx:]) + list(self._visible)
        if self._tail:
            parts.append(self._tail)
        return "\n".join(parts)

    def _char_count(self) -> int:
        return sum(len(x) for x in self._history) + \
            sum(len(x) for x in self._visible) + len(self._tail)

    def _trim(self) -> None:
        """超限裁剪：从历史头部丢弃行，游标同步位移"""
        chars = self._char_count()
        drop = 0
        while self._history and (len(self._history) > self._max_lines
                                 or chars > self._max_chars):
            dropped = self._history.pop(0)
            drop += 1
            chars -= len(dropped)
            if self._cursor > 0:
                self._cursor -= 1
            self._cursor = max(0, self._cursor)
        if drop:
            _logger.warning("OutputBuffer: trimmed %d lines from history head",
                            drop)