# M1：PySurface 增量渲染绑定（wezterm-surface 成品）
# 验证：写格子→增量 ANSI 字节；仅输出变化；未变化空字节。

import pywezterm


def test_init():
    s = pywezterm.Surface(40, 10)
    assert s.dimensions() == (40, 10)
    assert s.current_seqno() == 0


def test_first_frame_full_repaint():
    s = pywezterm.Surface(40, 10)
    s.set_cell(0, 0, "Hello")
    seq, data = s.get_changes_bytes(0)
    assert b"Hello" in data
    assert b"1;1H" in data  # CUP 定位到 (1,1)


def test_delta_only_changed():
    s = pywezterm.Surface(40, 10)
    s.set_cell(0, 0, "Hello")
    seq1, _ = s.get_changes_bytes(0)
    # 修改第 1 格内容 → 增量只含该格变化
    s.set_cell(0, 0, "HellX")
    seq2, delta = s.get_changes_bytes(seq1)
    assert b"HellX" in delta
    assert b"Hello" not in delta


def test_unchanged_empty():
    s = pywezterm.Surface(40, 10)
    s.set_cell(0, 0, "Hello")
    seq1, _ = s.get_changes_bytes(0)
    # 无任何写入 → 无变化
    seq2, empty = s.get_changes_bytes(seq1)
    assert empty == b""


def test_new_cell_position():
    s = pywezterm.Surface(40, 10)
    s.set_cell(5, 1, "x", "red")
    seq, data = s.get_changes_bytes(0)
    # 内容包含 x；且（清屏后）有定位到第 2 行(0-based y=1)的序列
    assert b"x" in data
    assert b"\x1b[2;" in data  # 定位到第 2 行（0-based y=1）
    # 注：颜色由 termwiz renderer 内部按 terminfo/ANSI SGR 编码，此处不锁死具体序列