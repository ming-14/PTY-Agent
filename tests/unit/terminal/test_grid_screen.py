"""terminal/grid_screen.py 单元测试 — GridScreen

测试重点:
1. 继承 pyte.Screen 的基本 VT 解析能力（draw/cursor/display）
2. draw() auto-wrap 路径正确标记 _line_wrapped
3. 硬换行（\n）不标记 _line_wrapped
4. index() 行滚动捕获顶部行到 scrollback（不丢失）
5. resize() 加宽合并 WRAPPED 行 / 变窄拆分长行
6. capture_scrollback / capture_visible 序列化
7. SGR 颜色保留
8. clear_scrollback / reset 行为
9. 与 pyte 原生行为对比（无 scrollback 时退化一致）

需 pyte + wcwidth 已安装，否则跳过。
"""

import pytest

try:
    import pyte  # noqa: F401
    from pyte.streams import Stream
    _HAS_PYTE = True
except ImportError:
    _HAS_PYTE = False

pytestmark = pytest.mark.skipif(not _HAS_PYTE,
                                reason="pyte 未安装，跳过 GridScreen 测试")

from src.terminal.grid_screen import GridScreen  # noqa: E402
from src.terminal.grid import LINE_WRAPPED  # noqa: E402


def feed_vt(screen: GridScreen, vt: str) -> None:
    """通过 pyte.Stream 喂入 VT 序列（模拟 PTY 输出）"""
    Stream(screen).feed(vt)


def capture_visible(s: GridScreen, **kwargs) -> str:
    """GridScreen.capture_visible 移除后的测试辅助：
    先 sync pyte.buffer → Grid.visible，再从 Grid 序列化 visible 区。
    include_cursor=True 且未显式传 cursor_pos 时，取 pyte.cursor 当前位置。
    """
    s._sync_pyte_to_grid_visible()
    if kwargs.get("include_cursor") and "cursor_pos" not in kwargs:
        kwargs["cursor_pos"] = (s.cursor.y, s.cursor.x)
    return s._grid.capture_visible(**kwargs)


# ── 基本创建测试 ─────────────────────────────────────────────

class TestGridScreenInit:
    def test_default_size(self):
        s = GridScreen(80, 24, hlimit=100)
        assert s.columns == 80
        assert s.lines == 24
        assert s.scrollback_lines_count == 0

    def test_custom_size(self):
        s = GridScreen(120, 40, hlimit=5000)
        assert s.columns == 120
        assert s.lines == 40
        assert s.scrollback_lines_count == 0

    def test_grid_property(self):
        s = GridScreen(10, 3, hlimit=50)
        grid = s.grid
        assert grid.sx == 10
        assert grid.sy == 3
        assert grid.hlimit == 50

    def test_line_wrapped_init_false(self):
        s = GridScreen(5, 3, hlimit=10)
        assert all(not w for w in s._line_wrapped)
        assert len(s._line_wrapped) == 3


# ── draw 基本测试 ────────────────────────────────────────────

class TestDraw:
    def test_draw_simple_text(self):
        s = GridScreen(10, 3, hlimit=10)
        s.draw("hello")
        assert s.cursor.x == 5
        assert s.cursor.y == 0
        assert s.display[0].rstrip() == "hello"

    def test_draw_advances_cursor(self):
        s = GridScreen(10, 3, hlimit=10)
        s.draw("ab")
        s.draw("cd")
        assert s.cursor.x == 4
        assert s.display[0].rstrip() == "abcd"

    def test_draw_multiline_via_vt(self):
        s = GridScreen(10, 3, hlimit=10)
        feed_vt(s, "line1\r\nline2\r\n")
        assert s.display[0].rstrip() == "line1"
        assert s.display[1].rstrip() == "line2"


# ── auto-wrap 标记测试 ───────────────────────────────────────

class TestAutoWrap:
    def test_auto_wrap_sets_wrapped_flag(self):
        """列满后继续 draw 触发 auto-wrap，该行标记 _line_wrapped"""
        s = GridScreen(5, 3, hlimit=10)
        s.draw("abcdef")  # 第 6 字符触发 auto-wrap
        assert s._line_wrapped[0] is True
        # 第二行有 "f"
        assert s.display[1].rstrip() == "f"

    def test_no_wrap_without_decamw(self):
        """关闭 DECAWM 模式时不 auto-wrap，不标记"""
        from pyte import modes as mo
        s = GridScreen(5, 3, hlimit=10)
        s.mode.discard(mo.DECAWM)
        s.draw("abcdef")  # 列满后不换行，覆盖最后一格
        assert s._line_wrapped[0] is False

    def test_hard_break_no_wrapped_flag(self):
        """显式 \n 换行不标记 _line_wrapped（硬换行）"""
        s = GridScreen(10, 3, hlimit=10)
        feed_vt(s, "line1\nline2\n")
        # 两行都是硬换行，不应有 wrapped 标记
        assert s._line_wrapped[0] is False
        assert s._line_wrapped[1] is False

    def test_mixed_wrap_and_hard_break(self):
        """auto-wrap 行 + 硬换行混合"""
        s = GridScreen(5, 4, hlimit=10)
        # "abcde" 填满第一行，下一字符触发 wrap
        s.draw("abcdef")  # line0=abcde (wrapped), line1=f
        feed_vt(s, "\n")  # 硬换行到 line2
        s.draw("end")
        assert s._line_wrapped[0] is True   # auto-wrap
        assert s._line_wrapped[1] is False  # 硬换行后
        assert s._line_wrapped[2] is False  # "end" 行


# ── scrollback 捕获测试 ──────────────────────────────────────

class TestScrollbackCapture:
    def test_index_at_bottom_captures_top(self):
        """index() 在底部时捕获顶部行到 scrollback"""
        s = GridScreen(10, 2, hlimit=10)
        feed_vt(s, "line1\r\nline2\r\n")  # line0=line1, line1=line2, 第二次 \r\n 触发 index
        # 顶部 "line1" 应入 scrollback
        assert s.scrollback_lines_count >= 1
        scrollback = s.capture_scrollback()
        assert "line1" in scrollback

    def test_multiple_scrolls_accumulate(self):
        """多次 index 累积 scrollback"""
        s = GridScreen(5, 2, hlimit=100)
        for i in range(5):
            feed_vt(s, f"L{i}\n")
        # 5 次 linefeed，每次在底部触发 index，5 行入 scrollback
        # 但前几次填满 visible，后续才真正入 scrollback
        assert s.scrollback_lines_count >= 3

    def test_scrollback_respects_hlimit(self):
        """scrollback 不超过 hlimit"""
        s = GridScreen(3, 1, hlimit=3)
        for i in range(10):
            feed_vt(s, f"{i:02d}\n")
        assert s.scrollback_lines_count <= 3

    def test_scrollback_preserves_wrapped_flag(self):
        """scrollback 中的行保留原 WRAPPED 标记"""
        s = GridScreen(5, 2, hlimit=10)
        # line0: "abcde" auto-wrap → wrapped=True
        # line1: "f"
        # 然后 \n 触发 index，line0 入 scrollback
        s.draw("abcdef")  # line0=abcde(wrapped), line1=f
        feed_vt(s, "\n")  # 在 line1 末尾 linefeed → 在底部触发 index
        # line0 (wrapped) 应入 scrollback
        assert s.scrollback_lines_count == 1
        scroll_line = s.grid.get_scrollback_line(0)
        assert scroll_line is not None
        assert scroll_line.wrapped is True


# ── resize reflow 测试 ───────────────────────────────────────

class TestResize:
    def test_resize_same_size_noop(self):
        s = GridScreen(10, 3, hlimit=10)
        s.draw("hello")
        s.resize(3, 10)  # 相同尺寸
        assert s.display[0].rstrip() == "hello"

    def test_resize_widen_keeps_content(self):
        s = GridScreen(10, 2, hlimit=10)
        s.draw("hello")
        s.resize(2, 20)
        assert s.columns == 20
        assert s.display[0].rstrip() == "hello"

    def test_resize_widen_merges_wrapped(self):
        """加宽：auto-wrap 拆分的行应合并回单行"""
        s = GridScreen(5, 3, hlimit=10)
        s.draw("hello world")  # 11 字符在 5 列下 auto-wrap
        # 应产生 "hello"(wrapped) + " worl"(wrapped) + "d"
        assert s._line_wrapped[0] is True
        # 加宽到 20 列，应合并
        s.resize(3, 20)
        # 合并后内容应包含 "hello world"（可能底部对齐有前导空行）
        combined = "".join(line.rstrip() for line in s.display if line.strip())
        # 合并后应能在去掉空格后找到 "helloworld"
        assert "helloworld" in combined.replace(" ", "")

    def test_resize_narrow_splits_long(self):
        """变窄：长行应拆分，超出 visible 的进 scrollback"""
        s = GridScreen(20, 2, hlimit=100)
        s.draw("0123456789ABCDEFGHIJ")  # 20 字符填满一行
        s.resize(2, 5)  # 变窄到 5 列
        # 20 字符拆成 5+5+5+5 = 4 行，前 2 行进 scrollback
        assert s.columns == 5
        # scrollback 应有内容
        assert s.scrollback_lines_count >= 2

    def test_resize_preserves_scrollback(self):
        """resize 不丢失已有 scrollback"""
        s = GridScreen(5, 2, hlimit=100)
        # 填满 visible 并产生 scrollback
        feed_vt(s, "line1\nline2\nline3\n")
        assert s.scrollback_lines_count >= 1
        before_count = s.scrollback_lines_count
        before_scrollback = s.capture_scrollback()
        # resize
        s.resize(2, 10)
        # scrollback 内容应保留
        after_scrollback = s.capture_scrollback()
        # 至少 line1 应该还在
        assert "line1" in after_scrollback

    def test_resize_empty_screen(self):
        """resize 空屏幕不崩溃"""
        s = GridScreen(10, 3, hlimit=10)
        s.resize(5, 20)
        assert s.columns == 20
        assert s.lines == 5

    def test_resize_updates_line_wrapped(self):
        """resize 后 _line_wrapped 从 Grid.visible 同步"""
        s = GridScreen(5, 2, hlimit=10)
        s.draw("abcde")  # 填满一行，无 wrap
        s.resize(2, 10)
        # 加宽后内容应在一行，无 wrapped 标记
        assert all(not w for w in s._line_wrapped)


# ── ConPTY repaint 清除陈旧 _line_wrapped 测试 ──────────────

class TestConptyRepaintClearsWrappedFlag:
    """ConPTY repaint 用 CSI row;col H 显式定位每行后 draw 写入内容。

    pyte.draw 不更新 _line_wrapped（仅 auto-wrap 设 True，CSI 定位 + draw
    不清除旧值）。若行曾被 reflow 标记为 wrapped，repaint 后标记陈旧，
    下一次 reflow 会错误合并行 → 内容错乱堆叠。

    修复：draw() 在 cursor.x==0 写入字符时清除 _line_wrapped[cursor.y]。
    """

    def test_draw_at_x0_clears_stale_wrapped_flag(self):
        """行首写入字符时清除陈旧 wrapped 标记"""
        s = GridScreen(5, 3, hlimit=10)
        # 制造 auto-wrap：line0=abcde(wrapped), line1=f
        s.draw("abcdef")
        assert s._line_wrapped[0] is True

        # 模拟 ConPTY repaint：CSI 定位到行首 + draw 重写 line0
        feed_vt(s, "\x1b[1;1H")  # CSI 1;1H → cursor (0, 0)
        s.draw("XY")  # 在 line0 行首写入，应清除 _line_wrapped[0]
        assert s._line_wrapped[0] is False

    def test_conpty_repaint_then_resize_no_garble(self):
        """ConPTY repaint 清除陈旧标记后，resize 不再错误合并行。

        复现用户 bug：反复 resize 变窄后再变宽时内容错乱堆叠。
        """
        s = GridScreen(10, 4, hlimit=100)
        # 初始内容：两行独立内容（硬换行分隔）
        feed_vt(s, "Hello\r\nWorld\r\n")
        # line0="Hello", line1="World", line2="", line3=""
        assert s._line_wrapped[0] is False
        assert s._line_wrapped[1] is False

        # 第一次 resize 变窄到 5 列：reflow 拆分 + 标记 wrapped
        s.resize(4, 5)
        # "Hello"(5) 填满 line0，"World" 拆成 "World"(5)
        # reflow 可能产生 wrapped 标记
        # 不断言具体标记（取决于 reflow 算法），关键是 repaint 后要清除

        # 模拟 ConPTY repaint：用 CSI 逐行定位重写
        for row_idx in range(s.lines):
            feed_vt(s, f"\x1b[{row_idx + 1};1H")
            line_text = s.display[row_idx].rstrip()
            if line_text:
                s.draw(line_text)

        # repaint 后所有行应无 wrapped 标记（CSI 定位 + draw 清除）
        assert all(not w for w in s._line_wrapped), \
            f"ConPTY repaint 后仍有陈旧 wrapped 标记: {s._line_wrapped}"

        # 第二次 resize 加宽到 10 列：不应错误合并行
        s.resize(4, 10)
        # 验证内容未被错误合并：Hello 和 World 应在不同行，不应拼到同一行
        lines = [line.rstrip() for line in s.display]
        # 不应出现同时包含 Hello 和 World 的行（错误合并的标志）
        for line in lines:
            assert not ("Hello" in line and "World" in line), \
                f"resize 后内容被错误合并到同一行: {lines}"

    def test_auto_wrap_still_sets_flag_after_clear(self):
        """清除后 auto-wrap 仍能正确设置 wrapped 标记。

        确保清除逻辑不破坏 auto-wrap 路径：auto-wrap 设 True 在前，
        clear 作用在新行（cursor.y 已移动），不影响旧行。
        """
        s = GridScreen(5, 3, hlimit=10)
        s.draw("abcdefgh")  # 8 字符在 5 列下：line0=abcde(wrapped), line1=fgh
        assert s._line_wrapped[0] is True   # 旧行被 auto-wrap 标记
        assert s._line_wrapped[1] is False  # 新行被 clear（写入 'f' 时 x==0）


# ── resize ConPTY 语义测试 ───────────────────────────────────

class TestResizeConptySemantics:
    """GridScreen.resize 必须与 ConPTY 的 resize 行为严格一致：

    - 高度增长：内容锚顶，底部补空行，scrollback 不变，光标不动
    - 高度收缩：优先砍底部空行（光标不动）；不够则砍顶部行推入
      scrollback（内容上移，光标随文本行上移）
    - resize 后 pyte.buffer == Grid.visible（写回统一）

    违背这些语义会导致 resize 后 ConPTY 的绝对光标定位（\\x1b[row;colH）
    落在前端显示内容的中间 —— "光标在 dir 输出中间" bug。
    """

    PROMPT = "C:\\work>"

    def _make_screen(self, cols=80, rows=24, content_lines=42):
        """构造满屏会话：content_lines 行内容（含 prompt），产生 scrollback"""
        s = GridScreen(cols, rows, hlimit=1000)
        lines = ["LINE%02d-aaaaaaaaaaaaaaaaaaaa" % i
                 for i in range(1, content_lines)]
        lines.append(self.PROMPT)
        feed_vt(s, "\r\n".join(lines))
        return s

    def test_grow_keeps_scrollback_and_cursor(self):
        """grow: scrollback 不抽行进 visible，底部补空行，光标不动"""
        s = self._make_screen()  # 42 行内容，24 行屏幕 → 18 scrollback
        assert s._grid.hsize == 18
        assert (s.cursor.y, s.cursor.x) == (23, len(self.PROMPT))
        s.resize(30, 120)
        # scrollback 不变（不从 scrollback 提升行）
        assert s._grid.hsize == 18
        # visible: 24 行内容 + 6 空行（底部）
        assert s._grid.sy == 30
        for r in range(24):
            line = s._grid.get_visible_line(r)
            assert "".join(c.data for c in line.cells).strip() != "", \
                f"visible[{r}] 应为内容行"
        for r in range(24, 30):
            line = s._grid.get_visible_line(r)
            assert "".join(c.data for c in line.cells).strip() == "", \
                f"visible[{r}] 应为空行"
        # 光标不动
        assert (s.cursor.y, s.cursor.x) == (23, len(self.PROMPT))
        # pyte.buffer 与 Grid.visible 一致（写回）
        pyte_line = "".join(s.buffer[23][c].data for c in range(120)).rstrip()
        assert pyte_line == self.PROMPT

    def test_shrink_trims_bottom_blank_lines(self):
        """shrink: 底部有空行时先砍空行，光标/scrollback 不变"""
        s = self._make_screen()
        s.resize(30, 120)  # grow → 底部 6 空行
        s.resize(24, 80)   # shrink 回
        assert s._grid.hsize == 18
        assert (s.cursor.y, s.cursor.x) == (23, len(self.PROMPT))

    def test_shrink_full_screen_pushes_top_to_scrollback(self):
        """shrink: 满内容时顶部行推入 scrollback，光标随内容上移"""
        s = self._make_screen()  # 24 行全满，cursor (23, x)
        s.resize(18, 80)  # 砍 6 行
        # 顶部 6 行推入 scrollback
        assert s._grid.hsize == 18 + 6
        # 光标随内容上移 6 行
        assert (s.cursor.y, s.cursor.x) == (17, len(self.PROMPT))
        # 原 scrollback 内容不丢（最旧行仍在）
        sb = s.capture_scrollback()
        assert "LINE01" in sb

    def test_grow_after_top_push_keeps_cursor(self):
        """砍顶 shrink 后再 grow：底部补空行，光标保持当前位置"""
        s = self._make_screen()
        s.resize(18, 80)
        assert (s.cursor.y, s.cursor.x) == (17, len(self.PROMPT))
        s.resize(24, 80)
        # grow 不动 scrollback、光标不动
        assert (s.cursor.y, s.cursor.x) == (17, len(self.PROMPT))
        assert s._grid.hsize == 24

    def test_narrow_width_overflow_pushes_top(self):
        """宽度变窄 rewrap 行数增多：超出部分从顶部推入 scrollback"""
        s = GridScreen(10, 2, hlimit=100)
        feed_vt(s, "0123456789ABCDEF")  # 16 字符 wrap 成 2 行（10+6）
        assert s._grid.hsize == 0
        s.resize(2, 5)  # 变窄到 5 列：拆成 4 行（5+5+5+1），推 2 行入 scrollback
        assert s._grid.hsize == 2
        assert s._grid.sy == 2

    def test_resize_result_pyte_buffer_matches_grid(self):
        """resize 后 pyte.buffer 必须与 Grid.visible 完全一致"""
        s = self._make_screen()
        s.resize(30, 120)
        for r in range(30):
            grid_line = s._grid.get_visible_line(r)
            grid_text = "".join(c.data for c in grid_line.cells).rstrip()
            pyte_text = "".join(
                s.buffer[r][c].data for c in range(120)).rstrip()
            assert pyte_text == grid_text, f"row {r} 不一致"


# ── capture 测试 ─────────────────────────────────────────────

class TestCapture:
    def test_capture_scrollback_empty(self):
        s = GridScreen(10, 3, hlimit=10)
        assert s.capture_scrollback() == ""

    def test_capture_scrollback_after_scroll(self):
        s = GridScreen(10, 2, hlimit=10)
        feed_vt(s, "first line\nsecond line\nthird line\n")
        scrollback = s.capture_scrollback()
        # 应包含被推出 visible 的内容
        assert len(scrollback) > 0
        # 应以 \r\n 分隔行
        assert "\r\n" in scrollback

    def test_capture_visible_empty(self):
        s = GridScreen(10, 3, hlimit=10)
        result = capture_visible(s)
        # 空屏幕，可能只有定位序列或空字符串
        # 不崩溃即可
        assert isinstance(result, str)

    def test_capture_visible_with_content(self):
        s = GridScreen(10, 3, hlimit=10)
        s.draw("hello")
        result = capture_visible(s)
        assert "hello" in result
        # 应含 CSI row;1H 定位
        assert "\x1b[1;1H" in result

    def test_capture_visible_with_cursor(self):
        s = GridScreen(10, 3, hlimit=10)
        s.draw("hi")  # cursor 在 (2, 0)
        result = capture_visible(s, include_cursor=True)
        # 末尾应有光标定位 CSI 1;3H
        assert "\x1b[1;3H" in result
        assert "\x1b[?25h" in result  # 光标显示


# ── SGR 颜色保留测试 ─────────────────────────────────────────

class TestColorPreservation:
    def test_draw_colored_text_preserved_in_visible(self):
        """带颜色的 draw 在 capture_visible 中保留 SGR 序列"""
        s = GridScreen(10, 3, hlimit=10)
        feed_vt(s, "\x1b[31mred\x1b[0m")  # 红色 "red"
        result = capture_visible(s)
        # 应含红色 SGR
        assert "\x1b[31m" in result
        assert "red" in result

    def test_colored_text_preserved_in_scrollback(self):
        """带颜色的 scrollback 行在 capture_scrollback 中保留 SGR"""
        s = GridScreen(10, 1, hlimit=10)
        feed_vt(s, "\x1b[32mgreen\x1b[0m\n")  # 绿色 "green" + 换行触发 index
        scrollback = s.capture_scrollback()
        assert "\x1b[32m" in scrollback
        assert "green" in scrollback

    def test_bold_attribute_preserved(self):
        s = GridScreen(10, 3, hlimit=10)
        feed_vt(s, "\x1b[1mbold\x1b[0m")
        result = capture_visible(s)
        assert "\x1b[1m" in result


# ── reset / clear 测试 ───────────────────────────────────────

class TestReset:
    def test_clear_scrollback(self):
        s = GridScreen(10, 2, hlimit=10)
        feed_vt(s, "line1\nline2\nline3\n")
        assert s.scrollback_lines_count > 0
        s.clear_scrollback()
        assert s.scrollback_lines_count == 0

    def test_clear_scrollback_keeps_visible(self):
        s = GridScreen(10, 2, hlimit=10)
        feed_vt(s, "line1\nline2\nline3\n")
        s.clear_scrollback()
        # visible 区应仍有内容
        visible_text = capture_visible(s)
        assert len(visible_text) > 0

    def test_reset_clears_everything(self):
        s = GridScreen(10, 2, hlimit=10)
        feed_vt(s, "line1\nline2\nline3\n")
        s.reset()
        assert s.scrollback_lines_count == 0
        # visible 应为空
        for line in s.display:
            assert line.strip() == ""
        # _line_wrapped 重置
        assert all(not w for w in s._line_wrapped)


# ── 与 pyte 原生行为对比测试 ─────────────────────────────────

class TestPyteCompatibility:
    """GridScreen 在没有 scrollback 操作时应与 pyte.Screen 行为一致"""

    def test_draw_same_as_pyte(self):
        from pyte.screens import Screen
        gs = GridScreen(10, 3, hlimit=10)
        ps = Screen(10, 3)
        text = "hello world foo bar"
        gs.draw(text)
        ps.draw(text)
        assert gs.display == ps.display
        assert gs.cursor.x == ps.cursor.x
        assert gs.cursor.y == ps.cursor.y

    def test_vt_sequences_same_as_pyte(self):
        from pyte.screens import Screen
        gs = GridScreen(20, 5, hlimit=10)
        ps = Screen(20, 5)
        vt = "\x1b[2J\x1b[1;1HHello\x1b[31mRed\x1b[0m\nWorld"
        Stream(gs).feed(vt)
        Stream(ps).feed(vt)
        assert gs.display == ps.display
        assert gs.cursor.x == ps.cursor.x
        assert gs.cursor.y == ps.cursor.y

    def test_cursor_position_same_as_pyte(self):
        from pyte.screens import Screen
        gs = GridScreen(20, 5, hlimit=10)
        ps = Screen(20, 5)
        vt = "\x1b[3;5Habc"
        Stream(gs).feed(vt)
        Stream(ps).feed(vt)
        assert gs.cursor.x == ps.cursor.x
        assert gs.cursor.y == ps.cursor.y
        assert gs.display == ps.display
