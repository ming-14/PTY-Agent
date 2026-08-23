# M2b：Mux 复用器——真实 Pty+Terminal pane
# 验证：
#  1. add_pane 真实 spawn 子进程，reader 线程自动把输出喂进终端模型（pane_text 读到）。
#  2. capture writer 键盘路径：pane_key_down 把模式感知编码字节下发 pty，
#     交互子进程回显键入内容（证明"编码→pty"闭环）。
# 闭环（应答自动回写 pty writer，避免子进程等 DSR 应答卡死）由 reader 线程承担。

import os
import time

import pywezterm


def _shell_echo(tag):
    """跨平台返回一个输出 tag 后立即退出的命令"""
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", f"echo {tag}"]
    return ["/bin/sh", "-c", f"echo {tag}"]


def _shell_interactive():
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe")]
    return ["/bin/sh"]


def _wait_text(m, pane, needle, timeout=6.0):
    """轮询 pane_text 直到包含 needle，返回最终文本"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = m.pane_text(pane)
        if needle in t:
            return t
        time.sleep(0.05)
    return m.pane_text(pane)


def test_mux_spawn_feed():
    """add_pane spawn 真实子进程 → reader 线程喂终端 → pane_text 读到输出"""
    m = pywezterm.Mux(80, 24)
    try:
        pane = m.add_pane(_shell_echo("M2B_FEED_OK"))
        assert pane == 0
        # 等待子进程输出被 reader 线程喂进终端模型
        t = _wait_text(m, pane, "M2B_FEED_OK")
        assert "M2B_FEED_OK" in t, t
        assert m.pane_try_wait(pane) == 0, m.pane_try_wait(pane)
    finally:
        m.close()


def test_mux_multiple_panes_isolated():
    """两 pane 各自 reader 线程，输出互不串扰"""
    m = pywezterm.Mux(120, 30)
    try:
        p0 = m.add_pane(_shell_echo("ALPHA"))
        p1 = m.add_pane(_shell_echo("BETA"))
        assert _wait_text(m, p0, "ALPHA").__contains__("ALPHA")
        assert _wait_text(m, p1, "BETA").__contains__("BETA")
        t0, t1 = m.pane_text(p0), m.pane_text(p1)
        assert "BETA" not in t0, t0
        assert "ALPHA" not in t1, t1
    finally:
        m.close()


def test_mux_keyboard_encoding_downstream():
    """capture writer 键盘路径：键入经编码下发 pty，交互 shell 回显键入内容"""
    m = pywezterm.Mux(80, 24)
    try:
        pane = m.add_pane(_shell_interactive())
        time.sleep(0.3)  # 等 shell 就绪
        text = "M2B_KEY"
        for ch in text:
            out = m.pane_key_down(pane, ch, 0)
            assert isinstance(out, bytes) and out, out
            time.sleep(0.02)
        m.pane_key_down(pane, "Enter", 0)
        # 键入字符被 shell 回显到终端模型（Windows cmd 逐字回显）
        t = _wait_text(m, pane, text)
        assert text in t, t
    finally:
        m.close()


def test_mux_close_pane_idempotent():
    m = pywezterm.Mux(80, 24)
    pane = m.add_pane(_shell_interactive())
    m.close_pane(pane)
    m.close_pane(pane)  # 幂等
    m.close()           # 再全量关闭也不报错