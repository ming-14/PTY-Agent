"""ScrollbackScreen — pyte 屏幕 + 滚动历史（pty 模式）

基于 pyte.Screen 的终端仿真屏幕，在上滚时把滚出顶端的行捕获进
滚动历史（deque，maxlen 上限），供"全量输出 = 滚动历史 + 可见屏幕"
的 read 语义使用。

pyte 说明：
- screen.buffer 为 {y: {x: Char}} 稀疏结构，行文本需按 x 排序拼接。
- 可见屏幕按已设置单元格提取，并去除尾部空行（宽度填充不进入输出）。
- 注意：不得覆盖 pyte.Screen.reset() —— reset 负责初始化 charset/
  g0_charset/g1_charset 等关键状态。
"""

import logging
from collections import deque
from typing import List

import pyte

from ...config import MAX_HISTORY_LINES

_logger = logging.getLogger("pty-session")


class ScrollbackScreen(pyte.Screen):
    """带滚动历史的终端屏幕

    覆写 index()：光标在底边距触发上滚时，将滚出顶行 rstrip 后
    追加进历史 deque（自动限长）。

    Attributes:
        scrollback_limit: 历史最大行数。
    """

    def __init__(self, cols: int = 80, rows: int = 24,
                 scrollback: int = MAX_HISTORY_LINES):
        super().__init__(columns=cols, lines=rows)
        self.scrollback_limit = scrollback
        self._history: deque = deque(maxlen=scrollback)
        self._last_hist_len = 0

    # ── 历史捕获 ──

    def index(self) -> None:
        """光标下移；底边距时上滚并捕获滚出顶行（pyte 调用时机）"""
        top, bottom = (self.margins if self.margins
                       else (0, self.lines - 1))
        if self.cursor.y == bottom:
            self._history.append(self._line_text(top).rstrip())
        super().index()

    def _line_text(self, y: int) -> str:
        """将屏幕第 y 行的 Char 矩阵渲染为字符串"""
        return "".join(c.data for _, c in sorted(self.buffer[y].items()))

    # ── 读取接口 ──

    @property
    def history_len(self) -> int:
        """滚动历史当前行数"""
        return len(self._history)

    def history_lines(self) -> List[str]:
        """当前滚动历史全部行"""
        return list(self._history)

    def drain_new_history(self) -> List[str]:
        """返回自上次调用以来新增的历史行并推进游标

        每次渲染后调用，把滚出的新行交给下游（输出缓冲）。

        Returns:
            新滚出的行列表。
        """
        rows = list(self._history)[self._last_hist_len:]
        self._last_hist_len = len(self._history)
        return rows

    def visible_lines(self) -> List[str]:
        """当前可见屏幕行（仅取已设置单元格，去除宽度/对齐填充与尾部空行）"""
        rows = [self._line_text(y).rstrip() for y in range(self.lines)]
        while rows and rows[-1] == "":
            rows.pop()
        return rows
