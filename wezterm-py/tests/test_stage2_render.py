# 阶段2：渲染原语下沉（wezterm-term 渲染能力暴露）
# 覆盖 current_seqno / changed_stable_rows / logical_lines，
# 供宿主做增量差分与逻辑行重组（替代手写逐行签名对比 + wrap 拼接）。

import pywezterm


def test_current_seqno_increments():
    t = pywezterm.Terminal(30, 5)
    s0 = t.current_seqno()
    t.feed(b"abc")
    assert t.current_seqno() > s0


def test_changed_stable_rows_dirty():
    """feed 后在基线 seqno 上报告变化行（不精确断言具体行号，只验证增量性）"""
    t = pywezterm.Terminal(30, 5)
    t.feed(b"line A\r\nline B")
    baseline = t.current_seqno()
    t.feed(b"Z")  # 追加到当前（line B）行末尾 → 仅该行变脏
    changed = t.changed_stable_rows(baseline)
    assert len(changed) >= 1, changed
    # 未变化的 line A（稳定行 0）不应报告
    assert not any(s == 0 for s in changed), changed


def test_logical_lines_wraps_raw():
    """超宽输出 wrap：logical_lines 把跨物理行的逻辑行正确重组"""
    t = pywezterm.Terminal(40, 6)
    wild = "LONG_" * 30  # 超过 40 列，必然 wrap
    t.feed(wild.encode() + b"\r\n")
    lines = t.logical_lines()
    # 可见区有 5 个逻辑行（首行为超宽重组后的完整逻辑行，其余为空）
    texts = ["".join(c[1] for c in row[2]) for row in lines[:2]]
    merged = "".join(texts)
    assert wild in merged, merged  # wrap 重组成完整逻辑行


def test_logical_lines_struct():
    """logical_lines 每项 (first_stable, last_stable, cells) 且稳定区间递增"""
    t = pywezterm.Terminal(30, 5)
    t.feed(b"hello\r\nworld\r\n")
    rows = t.logical_lines()
    assert all(isinstance(r, tuple) and len(r) == 3 for r in rows)
    for i in range(1, len(rows)):
        assert rows[i][0] > rows[i - 1][1]  # 稳定区间不重叠且递增


def test_logical_lines_respect_scroll():
    """视图滚动后 logical_lines 反映更早历史行"""
    t = pywezterm.Terminal(30, 4)
    for i in range(1, 21):
        t.feed(f"line {i}\r\n".encode())
    t.scroll(10)
    texts = ["".join(c[1] for c in row[2]) for row in t.logical_lines()]
    merged = "".join(texts)
    assert "line 10" in merged
    assert "line 20" not in merged  # 末行已滚出视图