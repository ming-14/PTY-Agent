# M2c：Mux 复用器——渲染合成 + 命中路由 + 滚动
# 验证：
#  1. render() 把各 pane 合成进整屏 Surface，首帧全量、无变化帧空增量；
#  2. 增量只含变化内容（不整屏重绘）；
#  3. 滚动后 render 反映视图平移（生成了增量字节），再回落到底部内容复原；
#  4. mouse 命中路由 + 坐标换算（整屏 → pane 内），未命中报错。

import os
import sys
import time

import pywezterm
import pytest


def _shell_echo(tag):
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", f"echo {tag}"]
    return ["/bin/sh", "-c", f"echo {tag}"]


def _shell_interactive():
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe")]
    return ["/bin/sh"]


def _wait_text(m, pane, needle, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = m.pane_text(pane)
        if needle in t:
            return t
        time.sleep(0.05)
    return m.pane_text(pane)


def test_mux_render_first_full_second_empty():
    """首帧把两 pane 全部合成出字节；无变化帧返回空增量"""
    m = pywezterm.Mux(80, 24)
    try:
        p0 = m.add_pane(_shell_echo("ONE"))
        p1 = m.add_pane(_shell_echo("TWO"))
        assert _wait_text(m, p0, "ONE")
        assert _wait_text(m, p1, "TWO")
        time.sleep(0.2)  # 等 reader 线程把输出喂进终端模型再取帧基线
        b0, cr, cc, cv = m.render()
        assert b0, "首帧应有字节"
        assert b"ONE" in b0
        assert b"TWO" in b0
        b1, _, _, _ = m.render()
        assert b1 == b"", "无变化帧应返回空增量"
    finally:
        m.close()


def test_mux_render_delta_only_changed():
    """增量只含变化内容：第二帧只带新增内容字节"""
    m = pywezterm.Mux(80, 24)
    try:
        p = m.add_pane(_shell_interactive())
        time.sleep(0.3)
        m.pane_write(p, b"echo ZZZ_DELTA_1\r\n")
        assert _wait_text(m, p, "ZZZ_DELTA_1")
        _ = m.render()
        m.pane_write(p, b"echo ZZZ_DELTA_2\r\n")
        assert _wait_text(m, p, "ZZZ_DELTA_2")
        b1, _, _, _ = m.render()
        assert b1, "新内容帧应有增量"
        assert b"ZZZ_DELTA_2" in b1
        b2, _, _, _ = m.render()
        assert b2 == b"", "再次无变化帧应为空"
    finally:
        m.close()


def test_mux_scroll_render_reflects_and_restores():
    """滚动后 render 反映视图平移；回落到底部内容复原"""
    m = pywezterm.Mux(100, 20)
    try:
        p = m.add_pane(_shell_interactive())
        time.sleep(0.3)
        for i in range(40):
            m.pane_write(p, ("echo LINE%02d\r\n" % i).encode())
            time.sleep(0.02)
        assert _wait_text(m, p, "LINE39")
        t_bottom = m.pane_text(p)
        assert t_bottom and "LINE39" in t_bottom
        m.render()
        m.pane_scroll(p, 8)
        t_scroll = m.pane_text(p)
        assert t_scroll != t_bottom, "滚动应改变可见内容"
        b1, _, _, _ = m.render()
        assert b1, "滚动应产生增量字节"
        m.pane_scroll_to_bottom(p)
        assert m.pane_text(p) == t_bottom, "回落到底部内容应复原"
    finally:
        m.close()


def test_mux_mouse_hit_routing():
    """整屏坐标命中 pane 并换算到 pane 内（无报错）；未命中报错。
    注：交互 shell 未开启鼠标追踪，mouse 编码返回空字节属正常，只验证路由不报错。"""
    m = pywezterm.Mux(80, 24)
    try:
        p0 = m.add_pane(_shell_interactive())
        p1 = m.add_pane(_shell_interactive())
        time.sleep(0.3)
        rects = m.pane_rects()
        # 左 pane 命中 → 不报错（路由到 pane0）
        m.mouse(5, 5, "press", "left", 0)
        # 右 pane 命中 → 坐标换算到 pane1 内（不报错）
        rx = rects[1][0] + 5
        m.mouse(rx, 5, "press", "left", 0)
        # 未命中（整屏外）→ 抛错
        with pytest.raises(Exception):
            m.mouse(9999, 9999, "press", "left", 0)
    finally:
        m.close()


def test_mux_set_focus_and_resize():
    """set_focus / resize 更新焦点与各 pane 矩形尺寸"""
    m = pywezterm.Mux(80, 24)
    try:
        m.add_pane(_shell_interactive())
        m.add_pane(_shell_interactive())
        time.sleep(0.2)
        m.set_focus(1)
        assert m.focused() == 1
        m.resize(120, 30)
        assert m.dimensions() == (120, 30)
        rects = m.pane_rects()
        assert (rects[0][2], rects[0][3]) == (60, 30)
        assert (rects[1][0], rects[1][2]) == (60, 60)
    finally:
        m.close()


def test_mux_sep_status_layout_controls():
    """分隔线 / 指定分割列 / 状态栏行 / 状态文本 布局控制与增量合成"""
    m = pywezterm.Mux(80, 24)
    try:
        m.add_pane(_shell_interactive())
        m.add_pane(_shell_interactive())
        time.sleep(0.3)
        # 启用分隔线：左 pane 占中点宽，右 pane 从分隔线 +1 起
        m.set_sep(True)
        rect = m.pane_rects()
        assert rect[0] == (0, 0, 40, 24)
        assert (rect[1][0], rect[1][2]) == (41, 39), rect
        # 指定分割列
        m.set_split_col(50)
        rect = m.pane_rects()
        assert (rect[0][2], rect[1][0], rect[1][2]) == (50, 51, 29), rect
        # 预留状态栏行：pane 高度缩减
        m.set_status_rows(1)
        rect = m.pane_rects()
        assert (rect[0][3], rect[1][3]) == (23, 23), rect
        # 状态文本参与 render 输出；未变化时再 render 为空增量
        m.set_status("STATUS_BAR_X")
        b0, _, _, _ = m.render()
        assert b"STATUS_BAR_X" in b0, b0
        b1, _, _, _ = m.render()
        assert b1 == b"", "状态/分隔线未变化时无增量"
    finally:
        m.close()