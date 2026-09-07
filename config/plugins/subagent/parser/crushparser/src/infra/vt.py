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


def _detect_size(vt_text: str, default_cols: int = 120, default_rows: int = 40) -> Tuple[int, int]:
    """从 VT 序列中扫描光标定位，推断所需屏幕尺寸。"""
    max_row = 0
    max_col = 0
    for m in _CUP_RE.finditer(vt_text):
        r = int(m.group(1))
        c = int(m.group(2))
        if r > max_row:
            max_row = r
        if c > max_col:
            max_col = c

    # 最长连续 ─ / ▀ / ╱ 行宽度（分隔线横跨整个终端宽度）
    separator_width = 0
    for line in _iter_vt_lines(vt_text):
        m = re.search(r"[─▀╱]{10,}", line)
        if m:
            w = len(m.group(0))
            if w > separator_width:
                separator_width = w

    # 纯文本快照（无 CUP 定位）时，最长可见行宽度决定所需列数
    longest_visible = 0
    vt_lines = _iter_vt_lines(vt_text)
    for line in vt_lines:
        stripped = line.strip()
        if len(stripped) > longest_visible:
            longest_visible = len(stripped)

    cols = max(max_col, separator_width + 2, longest_visible, default_cols)

    # 检测右侧栏关键词（New Session / Untitled Session / Skills），推算列位置
    for keyword in ("New Session", "Untitled Session", "Skills"):
        idx = vt_text.find(keyword)
        if idx < 0:
            continue
        before = vt_text[:idx]
        cups_before = list(re.finditer(r"\x1b\[(\d+);(\d+)H", before))
        if not cups_before:
            continue
        last = cups_before[-1]
        cup_col = int(last.group(2))
        after_cup = vt_text[last.end():idx]
        visible = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", after_cup)
        required = cup_col + len(visible) + 20
        if required > cols:
            cols = required

    rows = max(max_row, len(vt_lines), default_rows)
    return cols, rows


def _iter_vt_lines(vt_text: str) -> List[str]:
    """将 VT 流按光标定位拆分为屏幕行（用于宽度统计）。"""
    if not vt_text:
        return []
    parts = re.split(r"(\x1b\[\d+;\d+H|\r\n|\r|\n)", vt_text)
    lines: List[str] = []
    buf = ""
    for p in parts:
        if re.fullmatch(r"\x1b\[\d+;\d+H", p):
            if buf.strip():
                lines.append(buf)
            buf = ""
        elif p in ("\r\n", "\r", "\n"):
            if buf.strip():
                lines.append(buf)
            buf = ""
        else:
            buf += p
    if buf.strip():
        lines.append(buf)
    return lines


def _render_screen(screen) -> List[str]:
    """安全渲染 pyte 屏幕 buffer 为文本行。"""
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
    # 转换裸 \n 为 \r\n（pyte 的 LF 不重置列，需要 CR+LF）
    if "\r\n" not in vt_text and "\n" in vt_text:
        vt_text = vt_text.replace("\n", "\r\n")
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