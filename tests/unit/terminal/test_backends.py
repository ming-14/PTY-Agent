"""terminal/backends 单元测试 — wezterm-py 终端模拟后端

验证：
1. wezterm 后端在 VT 输入下产生正确的可见文本 / 光标 / 颜色。
2. TerminalScreen 门面对 wezterm 后端行为一致。
3. scrollback 捕获 / 清空 / resize 等后端能力。
"""

import pytest

from src.terminal.backends import (
    WeztermBackend,
    render_plain,
    render_ansi,
    build_cursor_seq,
)
from src.terminal.screen import TerminalScreen

import src.terminal.backends as _backends_mod

pytestmark = pytest.mark.skipif(not _backends_mod._HAS_WEZTERM, reason="wezterm-py 不可用")


def test_backend_basic_text():
    backend = WeztermBackend(40, 10)
    backend.feed(b"Hello World\r\nSecond line")
    text = render_plain(backend.cells())
    assert text == "Hello World\nSecond line", text
    # 光标位于第二行末尾（0-based row=1）
    x, y, visible = backend.cursor()
    assert (x, y) == (len("Second line"), 1), (x, y)
    assert visible is True


def test_backend_cjk_no_extra_space():
    """CJK 宽字符不得在字符间产生占位空格（历史 bug 回归）"""
    backend = WeztermBackend(40, 10)
    backend.feed("我喜欢你".encode("utf-8"))
    text = render_plain(backend.cells())
    assert text == "我喜欢你", repr(text)


def test_backend_color_ansi():
    backend = WeztermBackend(40, 10)
    backend.feed(b"\x1b[31mRed Text\x1b[0m")
    ansi = render_ansi(backend.cells())
    assert "Red Text" in ansi
    assert "\x1b[" in ansi


def test_backend_scrollback_and_clear():
    backend = WeztermBackend(20, 5)
    for i in range(20):
        backend.feed(f"line {i}\r\n".encode())
    sb = backend.capture_scrollback()
    assert sb, "scrollback 不应为空"
    assert "line 0" in sb, sb[:200]
    assert backend.scrollback_lines_count > 0
    backend.clear_scrollback()
    assert backend.scrollback_lines_count == 0
    # 清 scrollback 后可见区仍保留最后一行
    text = render_plain(backend.cells())
    assert "line 19" in text, text


def test_backend_reset():
    backend = WeztermBackend(40, 10)
    backend.feed(b"Hello")
    backend.reset()
    text = render_plain(backend.cells())
    assert "Hello" not in text, repr(text)


def test_screen_backend():
    """TerminalScreen 门面：wezterm 后端喂输入 → 正确 plain snapshot / 光标"""
    screen = TerminalScreen(cols=40, rows=10)
    assert screen.available
    assert screen.backend_name == "wezterm"
    screen.feed(b"alpha\r\nbeta")
    snap = screen.snapshot()
    assert snap == "alpha\nbeta", snap
    loc = screen.get_cursor_location()
    assert loc["row"] == 2 and loc["col"] == len("beta") + 1, loc
    seq = screen.get_cursor_seq()
    assert seq.startswith("\x1b[2;"), seq


def test_screen_scrollback_api():
    screen = TerminalScreen(cols=20, rows=5)
    for i in range(15):
        screen.feed(f"row {i}\r\n".encode())
    sb = screen.capture_scrollback()
    assert sb
    assert screen.scrollback_lines_count > 0
    screen.clear_scrollback()
    assert screen.scrollback_lines_count == 0


def test_full_snapshot_combines_scrollback_and_visible():
    """--full 语义：scrollback 历史 + 当前可见区 = 全部内容（纯文本与 ANSI 两种模式）"""
    screen = TerminalScreen(cols=30, rows=5)
    for i in range(12):
        screen.feed(f"line {i}\r\n".encode())

    # 纯文本：scrollback（行间 \n，无尾 \n）+ 可见区，中间补一个换行
    sb = screen.capture_scrollback(keep_ansi=False)
    snap = screen.snapshot(keep_ansi=False)
    combined = sb + "\n" + snap
    assert "line 0" in sb and "line 11" in snap
    assert combined.splitlines() == [f"line {i}" for i in range(12)]

    # ANSI：scrollback 以 \r\n 结尾，可见区直接拼接即可
    screen2 = TerminalScreen(cols=30, rows=5)
    for i in range(12):
        screen2.feed(f"\x1b[31mline {i}\x1b[0m\r\n".encode())
    sb2 = screen2.capture_scrollback(keep_ansi=True)
    snap2 = screen2.snapshot(keep_ansi=True)
    combined2 = sb2 + snap2
    assert combined2.count("line") == 12
    assert "\x1b[" in combined2


def test_screen_resize():
    screen = TerminalScreen(cols=10, rows=5)
    screen.feed(b"hello")
    screen.resize(30, 8)
    assert screen.cols == 30 and screen.rows == 8
    assert "hello" in screen.snapshot()


def test_default_backend_prefers_wezterm():
    screen = TerminalScreen()
    assert screen.backend_name == "wezterm"

def test_color_to_sgr_rgb_hex():
    """RGB 真彩色（wezterm "#rrggbb" 格式）应转换为 38;2 序列"""
    from src.terminal.backends import color_to_sgr
    assert color_to_sgr("#ff0000", is_fg=True) == "38;2;255;0;0"
    assert color_to_sgr("#00ff00", is_fg=False) == "48;2;0;255;0"
    assert color_to_sgr("ff0000", is_fg=True) == "38;2;255;0;0"
    assert color_to_sgr("#invalid", is_fg=True) == ""
    assert color_to_sgr("#ff000", is_fg=True) == ""


def test_render_ansi_rgb_truecolor():
    """RGB 真彩色内容应完整渲染为 38;2 序列（回归：刷新后颜色消失）"""
    backend = WeztermBackend(40, 10)
    backend.feed(b"\x1b[38;2;255;0;0mRED\x1b[0m\x1b[48;2;0;0;255mBG\x1b[0m")
    ansi = render_ansi(backend.cells())
    assert "\x1b[38;2;255;0;0mRED" in ansi, ansi
    assert "\x1b[48;2;0;0;255mBG" in ansi, ansi


def test_screen_mode_restore_seq():
    """DECSET 模式跟踪与恢复序列（鼠标追踪/备用屏幕/光标/paste）"""
    s = TerminalScreen(cols=40, rows=8)
    assert s.mode_restore_seq() == ""
    assert s.is_mouse_tracking() is False

    s.feed(b"\x1b[?1049h\x1b[?1002h\x1b[?1006h\x1b[?25lhello")
    assert s.is_alt_screen() is True
    assert s.is_mouse_tracking() is True
    seq = s.mode_restore_seq()
    assert "\x1b[?1049h" in seq
    assert "\x1b[?1002h" in seq
    assert "\x1b[?1006h" in seq
    assert "\x1b[?25l" in seq

    s.feed(b"\x1b[?1002l\x1b[?25h\x1b[?1049l")
    assert s.is_mouse_tracking() is False
    assert s.is_alt_screen() is False
    assert s.mode_restore_seq() == ""


def test_screen_mode_tracking_cross_feed():
    """模式序列跨 feed 边界（尾部窗口拼接）应正确跟踪"""
    s = TerminalScreen(cols=40, rows=8)
    s.feed(b"\x1b[?10")
    s.feed(b"03h")
    assert s.is_mouse_tracking() is True
    assert "\x1b[?1003h" in s.mode_restore_seq()


def test_screen_bracketed_paste_mode():
    s = TerminalScreen(cols=40, rows=8)
    s.feed(b"\x1b[?2004h")
    assert "\x1b[?2004h" in s.mode_restore_seq()
    s.feed(b"\x1b[?2004l")
    assert s.mode_restore_seq() == ""


# ── 选区 / OSC 52 剪贴板（阶段4）────────────────────────────────────────


def test_screen_selection_region_text():
    """TerminalScreen 选区：区域选择 → 取选中文本"""
    s = TerminalScreen(cols=40, rows=8)
    s.feed(b"hello world\r\nsecond line\r\n")
    assert not s.selection_active()
    assert s.selection_text() == ""
    s.selection_set(0, 6, 0, 10)
    assert s.selection_active()
    assert s.selection_text() == "world"
    s.selection_clear()
    assert not s.selection_active()
    assert s.selection_text() == ""


def test_screen_selection_word_and_line():
    s = TerminalScreen(cols=40, rows=8)
    s.feed(b"foo bar\r\nthird\r\n")
    s.selection_select_word(0, 4)  # "bar"
    assert s.selection_text() == "bar"
    s.selection_select_line(0, 0)
    assert s.selection_text() == "foo bar\n"


def test_screen_clipboard_callback_osc52():
    """OSC 52 剪贴板写 → TerminalScreen 回调收到 (selection, data)"""
    import base64

    s = TerminalScreen(cols=40, rows=8)
    got = []
    s.set_clipboard_callback(lambda sel, data: got.append((sel, data)))
    payload = base64.b64encode("剪贴板内容".encode()).decode()
    s.feed(f"\x1b]52;c;{payload}\x07".encode())
    assert got, "OSC 52 应触发剪贴板回调"
    assert got[-1] == ("clipboard", "剪贴板内容"), got[-1]


def test_screen_selection_unavailable_backend():
    """后端不可用时选区/剪贴板接口静默降级"""
    s = TerminalScreen(cols=40, rows=8)
    s._backend = None
    s.selection_set(0, 0, 1, 1)  # 不应抛异常
    assert s.selection_text() == ""
    assert s.selection_active() is False
    s.set_clipboard_callback(lambda sel, data: None)  # 不应抛异常
