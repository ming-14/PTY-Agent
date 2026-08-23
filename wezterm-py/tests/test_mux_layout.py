# M2a/M2b：Mux 复用器——布局矩形（左右二分）
# 验证 pane 布局树计算：第一个 pane 填满，第二个起左右各半。
# 使用真实 add_pane（spawn 子进程）；几何计算在 add_pane 内同步完成，
# 与子进程生命周期无关，测完 close 释放。

import os

import pywezterm


def _shell_argv():
    """跨平台返回一个立即退出的命令（仅用于占位建 pane，不关心输出）"""
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", "exit"]
    return ["/bin/sh", "-c", "true"]


def _mux(cols, rows, n):
    m = pywezterm.Mux(cols, rows)
    try:
        for _ in range(n):
            m.add_pane(_shell_argv())
        return m
    except Exception:
        m.close()
        raise


def test_mux_basic():
    m = pywezterm.Mux(120, 30)
    assert m.dimensions() == (120, 30)
    assert m.pane_count() == 0
    m.close()


def test_mux_single_pane_fullscreen():
    m = _mux(120, 30, 1)
    try:
        rects = m.pane_rects()
        assert rects == [(0, 0, 120, 30)]  # 第一个 pane 填满整屏
        assert m.focused() == 0
    finally:
        m.close()


def test_mux_two_panes_lr_split():
    m = _mux(120, 30, 2)
    try:
        rects = m.pane_rects()
        # 左右二分：左 pane 占左半，右 pane 占右半
        assert rects[0] == (0, 0, 60, 30)
        assert rects[1] == (60, 0, 60, 30)
        assert m.focused() == 1
    finally:
        m.close()


def test_mux_rect_width_odd():
    # 奇数宽：左半 = w/2（向下取整），右半 = w - w/2
    m = _mux(119, 30, 2)
    try:
        rects = m.pane_rects()
        assert rects[0] == (0, 0, 59, 30)
        assert rects[1] == (59, 0, 60, 30)
    finally:
        m.close()