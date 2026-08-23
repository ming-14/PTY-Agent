# 阶段1：终端状态查询绑定（wezterm-term 状态能力下沉到 pywezterm）
# 覆盖 is_mouse_grabbed / get_keyboard_encoding / is_alt_screen_active /
# bracketed_paste_enabled / focus_changed / get_title / get_current_dir /
# get_progress / get_semantic_zones / send_paste。
#
# 另含视图滚动原语：scroll / scroll_to_bottom / snapshot_lines（wrapped 结构），
# 供宿主渲染「开箱即用」，是统一 vendored 为唯一真源的一部分。

import pywezterm


def test_keyboard_encoding_default_xterm():
    t = pywezterm.Terminal(40, 10)
    assert t.get_keyboard_encoding() == "xterm"


def test_alt_screen_toggle():
    t = pywezterm.Terminal(40, 10)
    assert t.is_alt_screen_active() is False
    t.feed(b"\x1b[?1049h")
    assert t.is_alt_screen_active() is True
    t.feed(b"\x1b[?1049l")
    assert t.is_alt_screen_active() is False


def test_mouse_grabbed():
    t = pywezterm.Terminal(40, 10)
    assert t.is_mouse_grabbed() is False
    t.feed(b"\x1b[?1002h")
    assert t.is_mouse_grabbed() is True
    t.feed(b"\x1b[?1002l")
    assert t.is_mouse_grabbed() is False


def test_bracketed_paste():
    t = pywezterm.Terminal(40, 10)
    assert t.bracketed_paste_enabled() is False
    t.feed(b"\x1b[?2004h")
    assert t.bracketed_paste_enabled() is True


def test_title_icon_prefers_icon():
    t = pywezterm.Terminal(40, 10)
    t.feed(b"\x1b]1;icon\x1b\\\x1b]2;win\x1b\\")
    assert t.get_title() == "icon"


def test_current_dir_osc7():
    t = pywezterm.Terminal(40, 10)
    assert t.get_current_dir() is None
    t.feed(b"\x1b]7;file:///C:/work/project\x1b\\")
    assert t.get_current_dir() is not None
    assert t.get_current_dir().endswith("project")


def test_progress_none_default():
    t = pywezterm.Terminal(40, 10)
    assert t.get_progress() == ("none", None)


def test_semantic_zones():
    t = pywezterm.Terminal(40, 10)
    t.feed(b"\x1b]133;A\x1b\\$ ")
    t.feed(b"\x1b]133;B\x1b\\ls")
    t.feed(b"\x1b]133;C\x1b\\\r\nout")
    zones = t.get_semantic_zones()
    types = {z[4] for z in zones}
    assert "prompt" in types
    assert "input" in types
    assert "output" in types


def test_send_paste_default_nowrap():
    t = pywezterm.Terminal(40, 10)
    t.send_paste("abc")
    assert t.drain_written() == b"abc"


def test_snapshot_lines_wrapped_structure():
    """snapshot_lines 返回 (wrapped, cells) 行结构（宿主渲染用）"""
    t = pywezterm.Terminal(20, 4)
    t.feed(b"short\r\n")
    rows = t.snapshot_lines()
    assert all(isinstance(r, tuple) and len(r) == 2 for r in rows)
    assert rows[0][0] is False  # 短行不折行
    # 单元格元组：col,ch,fg,bg,bold,italic,underline,reverse,~,width
    assert rows[0][1][0][1] == "s"


def test_scroll_and_snapshot_reflect_offset():
    """scroll 修改视图偏移，snapshot_lines/text 跟随显示更早历史行"""
    t = pywezterm.Terminal(30, 4)
    for i in range(1, 21):
        t.feed(f"line {i}\r\n".encode())
    assert "line 20" in t.text()
    # 上滚 5 行查看更早历史：text 中出现更早行，末行 line20 不在当前视图
    t.scroll(10)
    scrolled = t.text()
    assert "line 10" in scrolled
    t.scroll_to_bottom()
    assert "line 20" in t.text()


def test_focus_changed():
    t = pywezterm.Terminal(40, 10)
    t.focus_changed(True)  # 不应抛异常