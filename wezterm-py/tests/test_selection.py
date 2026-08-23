# wezterm-py 库级自测：选区状态机（区域/词/行）+ OSC 52 剪贴板回调
# 独立于任何调用方，仅验证绑定层自身行为。
#
# 选区坐标模型：stable 行 + 列（跨 scrollback 与可见区，不受视图滚动影响）。
# 对 Terminal 级 API 直接传 stable 坐标（feed 后可见区首行即 stable 0）。

import pywezterm


def _term(text=None, cols=40, rows=8):
    """构造终端；text 非空则先喂入（每行 \r\n 结尾）"""
    t = pywezterm.Terminal(cols=cols, rows=rows)
    if text:
        t.feed(text.encode())
    return t


def _lines(term):
    """当前可见屏幕行文本列表（每行去尾空白）"""
    return [row.rstrip() for row in term.text().split("\n")]


# ---- 区域选择 -------------------------------------------------------------


def test_selection_region_single_row():
    t = _term("hello world\r\n")
    t.selection_set(0, 6, 0, 10)
    assert t.selection_text() == "world"
    assert t.selection_active()


def test_selection_region_across_rows():
    t = _term("abc\r\ndef\r\n")
    t.selection_set(0, 1, 1, 2)
    # 首行 [1,∞) → "bc"；末行 [0,2] → "def"
    assert t.selection_text() == "bc\ndef"


def test_selection_region_reversed():
    t = _term("abc\r\ndef\r\n")
    t.selection_set(1, 0, 0, 2)
    # lo=0 hi=1；start_col=2 end_col=0 → 首行 "c" 末行 "d"
    assert t.selection_text() == "c\nd"


def test_selection_region_into_scrollback():
    """跨 scrollback 行取文本（稳定行坐标不受视图滚动影响）"""
    t = _term(cols=20, rows=4)
    for i in range(12):
        t.feed(f"LINE_{i:02d}\r\n".encode())
    # 12 行 × 4 行视口 → 8 行 scrollback + 4 行可见（LINE_08..LINE_11）
    # 选 stable 2..stable 6 → LINE_02..LINE_06
    t.selection_set(2, 0, 6, 8)
    text = t.selection_text()
    assert text.startswith("LINE_02"), text
    assert "LINE_03" in text and "LINE_05" in text
    assert text.endswith("LINE_06"), text
    assert "LINE_07" not in text


def test_selection_no_selection_empty():
    t = _term("abc\r\n")
    assert t.selection_text() == ""
    assert not t.selection_active()


def test_selection_clear():
    t = _term("abc\r\ndef\r\n")
    t.selection_set(0, 0, 1, 2)
    assert t.selection_active()
    t.selection_clear()
    assert not t.selection_active()
    assert t.selection_text() == ""


# ---- 双击选词 / 三击选行 ----------------------------------------------------


def test_selection_select_word():
    t = _term("hello world\r\n")
    t.selection_select_word(0, 6)  # 'w' → "world"
    assert t.selection_text() == "world"
    assert t.selection_active()


def test_selection_select_word_punct_boundary():
    t = _term("foo,bar(baz)\r\n")
    t.selection_select_word(0, 1)  # "foo" 内
    assert t.selection_text() == "foo"
    t.selection_select_word(0, 5)  # "bar" 内
    assert t.selection_text() == "bar"
    t.selection_select_word(0, 10)  # "baz" 内
    assert t.selection_text() == "baz"


def test_selection_select_word_gap():
    """空白处双击：无选区"""
    t = _term("hello world\r\n")
    t.selection_select_word(0, 5)  # 空格
    assert not t.selection_active()
    assert t.selection_text() == ""


def test_selection_select_line_includes_newline():
    t = _term("hello world\r\nsecond\r\n")
    t.selection_select_line(0, 3)
    assert t.selection_text() == "hello world\n"


# ---- OSC 52 剪贴板回调 -----------------------------------------------------


def test_clipboard_callback_osc52():
    """应用发 OSC 52 写剪贴板 → Python 回调收到 (selection, data)"""
    t = _term()
    got = []
    t.set_clipboard_callback(lambda sel, data: got.append((sel, data)))
    # OSC 52：ESC ] 52 ; Pc ; Pd BEL（Pc=c 剪贴板，Pd=base64("你好")）
    import base64

    payload = base64.b64encode("你好".encode()).decode()
    t.feed(f"\x1b]52;c;{payload}\x07".encode())
    assert got, "OSC 52 应触发剪贴板回调"
    sel, data = got[-1]
    assert sel == "clipboard", sel
    assert data == "你好", data


def test_clipboard_callback_primary_selection():
    t = _term()
    got = []
    t.set_clipboard_callback(lambda sel, data: got.append((sel, data)))
    import base64

    payload = base64.b64encode(b"primary-data").decode()
    t.feed(f"\x1b]52;p;{payload}\x07".encode())
    assert got, "OSC 52 primary 应触发回调"
    assert got[-1] == ("primary", "primary-data"), got[-1]


def test_clipboard_callback_exception_survives():
    """回调抛异常不应崩终端（剪贴板写失败不崩）"""
    t = _term()

    def boom(sel, data):
        raise RuntimeError("clipboard boom")

    t.set_clipboard_callback(boom)
    import base64

    payload = base64.b64encode(b"x").decode()
    t.feed(f"\x1b]52;c;{payload}\x07".encode())
    # 终端仍可用
    t.feed(b"ok\r\n")
    assert "ok" in t.text()


def test_clipboard_callback_replace():
    """替换回调：旧回调不再收到，新回调生效"""
    t = _term()
    old = []
    new = []
    t.set_clipboard_callback(lambda sel, data: old.append((sel, data)))
    t.set_clipboard_callback(lambda sel, data: new.append((sel, data)))
    import base64

    payload = base64.b64encode(b"x").decode()
    t.feed(f"\x1b]52;c;{payload}\x07".encode())
    assert old == [], old
    assert new and new[-1][1] == "x"


# ---- make_all_lines_dirty -------------------------------------------------


def test_make_all_lines_dirty():
    """make_all_lines_dirty 把各行 last_change_seqno 上推到当前 seqno：
    以旧基线查询时，未变化的旧行也变脏（供选区高亮全量重绘）。"""
    t = pywezterm.Terminal(cols=20, rows=5)
    t.feed(b"row_a\r\n")  # 行0 写入（seqno = 当前）
    baseline = t.current_seqno()
    t.feed(b"row_b\r\n")  # 行1 写入；行0 未再变
    before = set(t.changed_stable_rows(baseline))
    assert before == {1}, before  # 基线：只有行1 脏
    t.make_all_lines_dirty()      # 全部行 seqno 上推到当前（含空行）
    after = set(t.changed_stable_rows(baseline))
    assert after == {0, 1, 2, 3, 4}, after  # 全部行脏（全量失效）


if __name__ == "__main__":
    test_selection_region_single_row()
    test_selection_region_across_rows()
    test_selection_region_reversed()
    test_selection_region_into_scrollback()
    test_selection_no_selection_empty()
    test_selection_clear()
    test_selection_select_word()
    test_selection_select_word_punct_boundary()
    test_selection_select_word_gap()
    test_selection_select_line_includes_newline()
    test_clipboard_callback_osc52()
    test_clipboard_callback_primary_selection()
    test_clipboard_callback_exception_survives()
    test_clipboard_callback_replace()
    test_make_all_lines_dirty()
    print("SELECTION TESTS PASSED")
