"""terminal/grid.py 单元测试 — Grid 数据结构

测试重点:
1. Grid 基本创建与属性
2. scroll_history / trim_history 的 scrollback 管理
3. capture_range / capture_scrollback / capture_visible 序列化
4. reflow 在各种 resize 场景下不丢内容:
   - 加宽：合并 WRAPPED 行
   - 变窄：拆分长行
   - 行数变化
   - scrollback + visible 混合
5. GridLine / GridCell 行为
"""

import pytest

from src.terminal.grid import (
    Grid, GridCell, GridLine, LINE_WRAPPED,
    _cell_to_sgr, _color_to_sgr,
)


# ── 辅助函数 ──────────────────────────────────────────────────

def make_line(text: str, wrapped: bool = False, cols: int = None) -> GridLine:
    """从字符串构造 GridLine（cols 默认等于 text 长度）"""
    if cols is None:
        cols = len(text)
    cells = [GridCell() for _ in range(cols)]
    for i, ch in enumerate(text):
        if i < cols:
            cells[i] = GridCell(data=ch)
    line = GridLine(cells=cells, flags=0)
    if wrapped:
        line.wrapped = True
    return line


def line_text(line: GridLine) -> str:
    """提取 GridLine 的可见文本（裁剪右侧默认 cell）"""
    w = line.width()
    return "".join(line.cells[i].data for i in range(w))


# ── GridCell 测试 ─────────────────────────────────────────────

class TestGridCell:
    def test_default_cell_is_default(self):
        cell = GridCell()
        assert cell.is_default() is True

    def test_non_default_data_not_default(self):
        cell = GridCell(data="x")
        assert cell.is_default() is False

    def test_bold_not_default(self):
        cell = GridCell(bold=True)
        assert cell.is_default() is False

    def test_copy_preserves_all_fields(self):
        cell = GridCell(data="A", fg="red", bg="blue", bold=True,
                        italics=True, underscore=True, reverse=True,
                        strikethrough=True)
        copied = cell.copy()
        assert copied == cell
        assert copied is not cell


# ── GridLine 测试 ─────────────────────────────────────────────

class TestGridLine:
    def test_empty_line(self):
        line = GridLine.empty(5)
        assert len(line.cells) == 5
        assert all(c.is_default() for c in line.cells)
        assert line.width() == 0
        assert line.wrapped is False

    def test_wrapped_flag(self):
        line = GridLine(cells=[GridCell(data="a")], flags=LINE_WRAPPED)
        assert line.wrapped is True
        line.wrapped = False
        assert line.wrapped is False
        assert line.flags == 0

    def test_width_trims_trailing_defaults(self):
        line = make_line("abc", cols=5)  # "abc" + 2 默认 cell
        assert line.width() == 3

    def test_width_zero_for_empty(self):
        line = GridLine.empty(5)
        assert line.width() == 0

    def test_copy_preserves_cells_and_flags(self):
        line = make_line("abc", wrapped=True, cols=5)
        copied = line.copy()
        assert copied.cells == line.cells
        assert copied.flags == line.flags
        assert copied is not line


# ── Grid 基本测试 ─────────────────────────────────────────────

class TestGridBasic:
    def test_init_creates_visible_lines(self):
        grid = Grid(cols=10, rows=3, hlimit=100)
        assert grid.sx == 10
        assert grid.sy == 3
        assert grid.hsize == 0
        assert grid.total_lines == 3
        assert len(grid.linedata) == 3

    def test_get_visible_line(self):
        grid = Grid(cols=5, rows=2, hlimit=100)
        line0 = grid.get_visible_line(0)
        assert line0 is not None
        assert line0.width() == 0
        assert grid.get_visible_line(2) is None  # 越界

    def test_get_scrollback_line_empty(self):
        grid = Grid(cols=5, rows=2, hlimit=100)
        assert grid.get_scrollback_line(0) is None


# ── scrollback 管理测试 ───────────────────────────────────────

class TestScrollback:
    def test_scroll_history_appends_line(self):
        grid = Grid(cols=5, rows=2, hlimit=100)
        line = make_line("hello")
        grid.scroll_history(line)
        assert grid.hsize == 1
        assert grid.total_lines == 3  # 1 scrollback + 2 visible
        assert grid.get_scrollback_line(0) is line

    def test_trim_history_respects_hlimit(self):
        grid = Grid(cols=5, rows=2, hlimit=3)
        for i in range(10):
            grid.scroll_history(make_line(f"line{i}"))
        assert grid.hsize == 3  # 不超过 hlimit
        assert grid.total_lines == 5  # 3 scrollback + 2 visible
        # 最旧的两行被裁剪，保留 line7, line8, line9
        assert line_text(grid.get_scrollback_line(0)) == "line7"
        assert line_text(grid.get_scrollback_line(2)) == "line9"

    def test_clear_scrollback(self):
        grid = Grid(cols=5, rows=2, hlimit=100)
        grid.scroll_history(make_line("scroll1"))
        grid.scroll_history(make_line("scroll2"))
        assert grid.hsize == 2
        grid.clear_scrollback()
        assert grid.hsize == 0
        assert grid.total_lines == 2  # 只剩 visible

    def test_clear_all(self):
        grid = Grid(cols=5, rows=2, hlimit=100)
        grid.scroll_history(make_line("scroll1"))
        grid.clear_all()
        assert grid.hsize == 0
        assert grid.total_lines == 2
        assert all(line.width() == 0 for line in grid.linedata)


# ── reflow 测试 ───────────────────────────────────────────────

class TestReflow:
    def test_reflow_widen_visible_keeps_content(self):
        """加宽：单行内容不变，cells 扩展到新列宽"""
        grid = Grid(cols=5, rows=2, hlimit=100)
        # 填充 visible
        grid.linedata[0] = make_line("hello", cols=5)
        grid.linedata[1] = make_line("world", cols=5)
        grid.reflow(10, 2)
        assert grid.sx == 10
        assert grid.sy == 2
        assert line_text(grid.get_visible_line(0)) == "hello"
        assert line_text(grid.get_visible_line(1)) == "world"

    def test_reflow_widen_merges_wrapped_lines(self):
        """加宽：WRAPPED 行应合并回单行

        ConPTY 语义：reflow 后内容锚顶。合并后只剩 1 行内容时，
        它位于 visible 第一行，底部补空行。
        """
        grid = Grid(cols=5, rows=2, hlimit=100)
        # 模拟 "hello world" 在 5 列下被拆成两行
        # line0: "hello" wrapped=True
        # line1: " worl" wrapped=False (剩余 "d" 丢失，简化)
        grid.linedata[0] = make_line("hello", wrapped=True, cols=5)
        grid.linedata[1] = make_line(" worl", wrapped=False, cols=5)
        pushed = grid.reflow(15, 2)
        # 合并后只有 1 行内容，锚顶 → visible[0]，底部补空行
        assert pushed == 0
        assert line_text(grid.get_visible_line(0)) == "hello worl"
        assert line_text(grid.get_visible_line(1)) == ""

    def test_reflow_narrow_splits_long_line(self):
        """变窄：长行应拆分为多行，超出 visible 的从顶部进 scrollback

        "0123456789" (10 列) → 4 列下拆成 "0123" + "4567" + "89"
        ConPTY 语义：先砍底部空行，仍超出则从顶部推入 scrollback。
        sy=2 时 visible = ["4567", "89"]，scrollback 含 ["0123"]。
        """
        grid = Grid(cols=10, rows=2, hlimit=100)
        # line0: "0123456789"（10 字符填满）
        grid.linedata[0] = make_line("0123456789", cols=10)
        grid.linedata[1] = GridLine.empty(10)
        pushed = grid.reflow(4, 2)
        assert grid.sx == 4
        # 拆成 3 行 + 1 空行 = 4 行：砍底部空行 → 3 行，推顶 1 行 → 2 行
        assert pushed == 1
        assert line_text(grid.get_visible_line(0)) == "4567"
        assert line_text(grid.get_visible_line(1)) == "89"
        # scrollback 应只包含推顶的 "0123"
        scrollback = grid.capture_scrollback()
        assert "0123" in scrollback
        assert "4567" not in scrollback

    def test_reflow_preserves_scrollback(self):
        """reflow 不丢失 scrollback 内容"""
        grid = Grid(cols=5, rows=2, hlimit=100)
        grid.scroll_history(make_line("scroll1"))
        grid.scroll_history(make_line("scroll2"))
        # visible
        grid.linedata[2] = make_line("vis1", cols=5)
        grid.linedata[3] = make_line("vis2", cols=5)
        original_hsize = grid.hsize
        grid.reflow(10, 2)
        # scrollback 行数可能变化（reflow 后重新计算），但内容不丢
        assert grid.hsize >= 0
        # 检查 scrollback 内容存在
        scrollback_text = grid.capture_scrollback()
        assert "scroll1" in scrollback_text
        assert "scroll2" in scrollback_text

    def test_reflow_empty_grid(self):
        """reflow 空 Grid 不崩溃"""
        grid = Grid(cols=5, rows=2, hlimit=100)
        grid.reflow(10, 3)
        assert grid.sx == 10
        assert grid.sy == 3
        assert grid.total_lines >= 3

    def test_reflow_same_size_noop(self):
        """reflow 到相同尺寸不崩溃，内容保持"""
        grid = Grid(cols=5, rows=2, hlimit=100)
        grid.linedata[0] = make_line("hello", cols=5)
        grid.linedata[1] = make_line("world", cols=5)
        grid.reflow(5, 2)
        assert line_text(grid.get_visible_line(0)) == "hello"
        assert line_text(grid.get_visible_line(1)) == "world"

    def test_reflow_hard_break_not_merged(self):
        """硬换行（非 WRAPPED）不被合并"""
        grid = Grid(cols=5, rows=2, hlimit=100)
        # 两个独立行（都未 wrapped）
        grid.linedata[0] = make_line("hello", wrapped=False, cols=5)
        grid.linedata[1] = make_line("world", wrapped=False, cols=5)
        grid.reflow(15, 2)
        # 加宽后仍然是两行（不合并）
        assert line_text(grid.get_visible_line(0)) == "hello"
        assert line_text(grid.get_visible_line(1)) == "world"


# ── capture 测试 ──────────────────────────────────────────────

class TestCapture:
    def test_capture_scrollback_empty(self):
        grid = Grid(cols=5, rows=2, hlimit=100)
        assert grid.capture_scrollback() == ""

    def test_capture_scrollback_with_content(self):
        grid = Grid(cols=10, rows=2, hlimit=100)
        grid.scroll_history(make_line("line1", cols=10))
        grid.scroll_history(make_line("line2", cols=10))
        result = grid.capture_scrollback()
        assert "line1" in result
        assert "line2" in result
        # 每行应以 \r\n 结尾
        assert result.endswith("\r\n")

    def test_capture_visible_with_position(self):
        """capture_visible 输出含 CSI row;col H 定位序列"""
        grid = Grid(cols=10, rows=2, hlimit=100)
        grid.linedata[0] = make_line("hello", cols=10)
        grid.linedata[1] = make_line("world", cols=10)
        result = grid.capture_visible()
        # 每行前应有 CSI row+1;1H
        assert "\x1b[1;1H" in result
        assert "\x1b[2;1H" in result
        assert "hello" in result
        assert "world" in result

    def test_capture_visible_with_cursor(self):
        grid = Grid(cols=10, rows=2, hlimit=100)
        result = grid.capture_visible(
            include_cursor=True,
            cursor_pos=(1, 2),  # row=1, col=2
            cursor_hidden=False,
        )
        # 末尾应有光标定位 CSI 2;3H + ?25h
        assert result.endswith("\x1b[2;3H\x1b[?25h")

    def test_capture_visible_cursor_hidden(self):
        grid = Grid(cols=10, rows=2, hlimit=100)
        result = grid.capture_visible(
            include_cursor=True,
            cursor_pos=(0, 0),
            cursor_hidden=True,
        )
        assert result.endswith("\x1b[1;1H\x1b[?25l")


# ── SGR 颜色序列化测试 ────────────────────────────────────────

class TestSGRSerialization:
    def test_default_cell_sgr(self):
        cell = GridCell()
        assert _cell_to_sgr(cell) == "\x1b[0m"

    def test_bold_sgr(self):
        cell = GridCell(bold=True)
        assert "1" in _cell_to_sgr(cell)

    def test_color_red_fg(self):
        cell = GridCell(fg="red")
        sgr = _cell_to_sgr(cell)
        assert "31" in sgr  # red fg = 31

    def test_color_brightblue_fg(self):
        cell = GridCell(fg="brightblue")
        sgr = _cell_to_sgr(cell)
        # brightblue 在 _ANSI_FG_BRIGHT 是 12, 90+12-8=94
        assert "94" in sgr

    def test_color_hex_rgb(self):
        cell = GridCell(fg="ff0000")  # red
        sgr = _cell_to_sgr(cell)
        assert "38;2;255;0;0" in sgr

    def test_color_default_no_sgr(self):
        assert _color_to_sgr("default", is_fg=True) == ""
        assert _color_to_sgr("default", is_fg=False) == ""

    def test_color_256_int(self):
        assert _color_to_sgr(196, is_fg=True) == "38;5;196"
