"""VT 序列处理：用 pyte 终端模拟器将带 ANSI/VT 序列的文本解析为屏幕行。

PTY-Agent 的 --keep-ansi 输出包含光标定位、颜色等 VT 序列，
pyte.Screen 模拟终端渲染，最终 buffer 即为屏幕可见内容。
"""
from __future__ import annotations

import re
from typing import List, Tuple

import pyte

from .logging import get_logger

_log = get_logger("vt")

# 光标定位序列：CSI 行;列 H
_CUP_RE = re.compile(r"\x1b\[(\d+);(\d+)H")


def _detect_size(vt_text: str, default_cols: int = 200, default_rows: int = 50) -> Tuple[int, int]:
    """从 VT 序列中扫描光标定位，推断所需屏幕尺寸。

    优先使用 CUP 序列的最大行列值，若无 CUP 则从文本行长度推断。
    加 20 列安全余量，避免状态栏最右侧模型名被截断。
    """
    max_row = 0
    max_col = 0
    for m in _CUP_RE.finditer(vt_text):
        r = int(m.group(1))
        c = int(m.group(2))
        if r > max_row:
            max_row = r
        if c > max_col:
            max_col = c

    # 无 CUP 时从文本行长度推断
    if max_col == 0:
        for line in vt_text.splitlines():
            # 不含 VT 控制字符的纯文本长度
            clean = strip_ansi(line)
            if len(clean) > max_col:
                max_col = len(clean)

    # 加 20 列安全余量，避免右侧内容被截断
    cols = max(max_col, default_cols) + 20
    rows = max(max_row, default_rows)
    return cols, rows


def _render_screen(screen) -> List[str]:
    """安全渲染 pyte 屏幕 buffer 为文本行。

    宽字符（如 █ 块字符、emoji）的 continuation cell data 为空字符串，
    渲染时跳过；空/缺失 cell 按空格处理。
    """
    cols = screen.columns
    lines: List[str] = []
    for y in range(screen.lines):
        row = screen.buffer[y]
        chars: List[str] = []
        for x in range(cols):
            cell = row.get(x)
            data = getattr(cell, "data", None) if cell is not None else None
            if data is None:
                chars.append(" ")
            elif data == "":
                # 宽字符 continuation：跳过
                continue
            else:
                chars.append(data)
        lines.append("".join(chars))
    return lines


def parse_screen(vt_text: str, columns: int = 0, rows: int = 0,
                 rstrip: bool = True) -> List[str]:
    """将带 VT 序列的文本解析为屏幕各行纯文本。

    Args:
        vt_text: PTY-Agent --keep-ansi 输出
        columns: 指定列数，0 则自动检测
        rows: 指定行数，0 则自动检测
        rstrip: 是否去除行尾空格

    Returns:
        屏幕各行文本列表
    """
    if columns <= 0 or rows <= 0:
        dc, dr = _detect_size(vt_text)
        if columns <= 0:
            columns = dc
        if rows <= 0:
            rows = dr

    screen = pyte.Screen(columns, rows)
    stream = pyte.Stream(screen)
    stream.feed(vt_text)

    lines = [line.rstrip() if rstrip else line for line in _render_screen(screen)]
    _log.debug("parse_screen: cols=%d rows=%d, non-empty=%d",
               columns, rows, sum(1 for l in lines if l))
    return lines


def strip_ansi(text: str) -> str:
    """剥离所有 ANSI/VT 控制序列，返回纯文本。"""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"\x1b\][^\x07]*\x07", "", text)
    text = re.sub(r"\x1b[@-Z\\-_]", "", text)
    return text
