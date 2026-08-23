# wezterm-py 库级自测：wezterm-term 终端模型 + 输入编码
# 独立于任何调用方，仅验证库自身行为。

import pywezterm


def test_terminal_model():
    t = pywezterm.Terminal(cols=40, rows=10)
    t.feed(b"hello world\r\n")
    t.feed(b"second line\x1b[31mred\x1b[0m")
    text = t.text()
    assert text.startswith("hello world"), text
    assert "second line" in text, text
    # 光标应位于第二行末尾（0-based row=1）
    row, col, visible = t.cursor()
    assert row == 1, (row, col)
    assert col == len("second line") + len("red"), (row, col)
    # snapshot 单元格：第二行包含红色前景（元组: col,ch,fg,bg,bold,italic,underline,reverse,width）
    snap = t.snapshot()
    assert len(snap) == 10
    red_cells = [c for c in snap[1] if c[2] != "default"]
    assert red_cells, snap[1]


def test_key_encoding():
    t = pywezterm.Terminal(cols=40, rows=10)
    # 普通模式方向键 → ESC [ A
    assert t.key_down("Up", 0) == b"\x1b[A", t.key_down("Up", 0)
    # 应用光标模式方向键 → ESC O A
    t2 = pywezterm.Terminal(cols=40, rows=10)
    t2.feed(b"\x1b[?1h")  # DECCKM 应用光标模式
    assert t2.key_down("Up", 0) == b"\x1bOA", t2.key_down("Up", 0)
    # 普通字符 + Shift → 大写
    t3 = pywezterm.Terminal(cols=40, rows=10)
    assert t3.key_down("a", 1 << 1) == b"A", t3.key_down("a", 1 << 1)


def test_mouse_encoding():
    t = pywezterm.Terminal(cols=40, rows=10)
    # 未启用鼠标上报 → 编码为空
    assert t.mouse(5, 3, "press", "left", 0) == b""
    # 鼠标上报（?1000h 按钮事件）+ SGR-1006 格式：
    # \x1b[<b;x;yM（按下）/ m（释放），坐标 1-based
    t.feed(b"\x1b[?1000h\x1b[?1006h")
    enc = t.mouse(5, 3, "press", "left", 0)
    assert enc.startswith(b"\x1b[<0;6;4") and enc.endswith(b"M"), enc
    enc2 = t.mouse(5, 3, "release", "left", 0)
    assert enc2.startswith(b"\x1b[<0;6;4") and enc2.endswith(b"m"), enc2
    # 右键 press
    enc3 = t.mouse(5, 3, "press", "right", 0)
    assert enc3.startswith(b"\x1b[<2;6;4"), enc3


def test_scrollback():
    t = pywezterm.Terminal(cols=20, rows=5)
    for i in range(20):
        t.feed(f"line {i}\r\n".encode())
    sb = t.scrollback()
    assert sb, "scrollback 不应为空"
    text = t.text()
    assert "line 19" in text, text


def test_resize_cursor_bind():
    """enable_conpty_quirks：resize 时光标绑定原文本行（与 Windows ConPTY 一致）

    旧行为（未启用 conpty_quirks）为"底部重力"：grow 时光标移到新视口底部，
    与 ConPTY 实测光标不一致，导致 resize 后按键回显在显示内容中间。
    """
    t = pywezterm.Terminal(cols=80, rows=24, scrollback=10000)
    # 40 行输出填满 scrollback + 视口（模拟 cmd 批量 echo 场景）
    for i in range(1, 41):
        t.feed(f"LINE{i}-aaaaaaaaaaaaaaaaaaaa\r\n".encode())
    # 光标定位到 prompt 行 (0-based 23, col 15)
    t.feed(b"\x1b[24;16H")
    assert t.cursor() == (23, 15, True), t.cursor()

    # grow 80x24 → 120x30：内容锚顶、光标绑定文本行，不应移到新视口底部
    t.resize(120, 30)
    assert t.cursor() == (23, 15, True), f"grow 后光标: {t.cursor()}"

    # shrink 回 80x24：光标仍绑定原文本行
    t.resize(80, 24)
    assert t.cursor() == (23, 15, True), f"shrink 后光标: {t.cursor()}"


if __name__ == "__main__":
    test_terminal_model()
    test_key_encoding()
    test_mouse_encoding()
    test_scrollback()
    test_resize_cursor_bind()
    print("ALL TESTS PASSED")
