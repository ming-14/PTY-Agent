"""窗格/渲染 e2e：真实 ConPTY 上运行程序，验证 Mux 合成渲染与 Pane 封装。

渲染差分/逻辑行 reflow 已下沉到 pywezterm.Mux（引擎单测覆盖），此处
验证 leaf 侧的 MuxPanel 门面能真实 spawn、合成两 pane + 分隔线、并增量输出。
"""

import re
import time

from leaf.drivers.pane import MuxPanel, Pane


def make_pane(*args, **kwargs):
    kwargs.pop("render_event", None)  # 新 Pane（单 pane Mux 封装）不再需要 render_event
    return Pane(*args, **kwargs)


def wait_text(pane, out, timeout=10.0):
    """轮询直到 pane.text() 含 out，返回最终文本"""
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        text = pane.text()
        if out in text:
            return text
        time.sleep(0.05)
    return text


def _shell_echo(tag):
    return ["cmd.exe", "/d", "/c", f"echo {tag}"]


def _wait_pane_text(mux, pid, out, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = mux.pane_text(pid)
        if out in t:
            return t
        time.sleep(0.05)
    return mux.pane_text(pid)


def test_two_panes_independent():
    # 两个独立单 pane Mux 同时运行各自程序，输出互不干扰
    p1 = make_pane(40, 10, _shell_echo("TOP_MARK_ONE"))
    p2 = make_pane(40, 10, _shell_echo("BOT_MARK_TWO"))
    try:
        t1 = wait_text(p1, "TOP_MARK_ONE")
        t2 = wait_text(p2, "BOT_MARK_TWO")
        assert "BOT_MARK_TWO" not in t1, "窗格 1 混入了窗格 2 的输出"
        assert "TOP_MARK_ONE" not in t2, "窗格 2 混入了窗格 1 的输出"
    finally:
        p1.close()
        p2.close()


def test_pane_resize_reflected_in_pty():
    p = make_pane(40, 10, ["cmd.exe", "/d", "/c", "pause"])
    try:
        p.resize(60, 20)
        deadline = time.monotonic() + 5
        size = (0, 0)
        while time.monotonic() < deadline:
            size = p.get_size()
            if size == (60, 20):
                break
            time.sleep(0.05)
        assert size == (60, 20), "pty 尺寸未随 resize 生效: {}".format(size)
    finally:
        p.close()


def test_pane_key_write_readback():
    p = make_pane(40, 10, ["cmd.exe", "/d", "/k"])
    try:
        p.write(b"echo CIRCUIT_OK\r")
        text = wait_text(p, "CIRCUIT_OK")
        assert "CIRCUIT_OK" in text
    finally:
        p.close()


def test_pane_scrollback_scroll():
    p = make_pane(60, 5, ["cmd.exe", "/d", "/c", "for /l %i in (1,1,20) do echo LINE_MARK_%i"])
    try:
        wait_text(p, "LINE_MARK_20")
        bottom = p.text()
        assert "LINE_MARK_20" in bottom
        p.scroll(5)
        scrolled = p.text()
        # 上滚 5 行：LINE_MARK_20 应消失、出现更早的行号
        assert "LINE_MARK_20" not in scrolled
        p.scroll_to_bottom()
        assert "LINE_MARK_20" in p.text()
    finally:
        p.close()


_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _plain(b: bytes) -> str:
    return _ANSI.sub("", b.decode("utf-8", "replace"))


def test_mux_compose_both_panes_and_separator():
    """MuxPanel 合成两 pane + 分隔线；首帧含两者内容，无变化帧空增量"""
    mux = MuxPanel(120, 30)
    try:
        left = mux.add_pane(_shell_echo("LEFT_MARK_ONE"))
        right = mux.add_pane(_shell_echo("RIGHT_MARK_TWO"))
        assert _wait_pane_text(mux, left, "LEFT_MARK_ONE")
        assert _wait_pane_text(mux, right, "RIGHT_MARK_TWO")
        time.sleep(0.2)  # 等 reader 线程喂完再取帧基线
        b0, cr, cc, cv = mux.render()
        plain = _plain(b0)
        assert "LEFT_MARK_ONE" in plain
        assert "RIGHT_MARK_TWO" in plain
        assert "│" in plain  # 分隔线已合成进渲染字节
        b1, _, _, _ = mux.render()
        assert b1 == b"", "无变化帧应返回空增量"
    finally:
        mux.close()