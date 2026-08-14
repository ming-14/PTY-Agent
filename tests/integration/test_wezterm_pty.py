"""WeztermPseudoTerminal 集成测试 — spawn/读写/退出码/close（真实 cmd 进程）

验证 wezterm-py PTY 后端满足 PseudoTerminal 契约：
输出捕获、进程 pid、退出码、resize、close 清理。
"""

import os
import time

import pytest

from src.pty.wezterm_pty import WeztermPseudoTerminal, _HAS_WEZTERM

pytestmark = pytest.mark.skipif(not _HAS_WEZTERM, reason="wezterm-py 不可用")


def _read_until(pty, marker, timeout=8.0):
    out = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        chunk = pty.read(4096)
        if not chunk:
            break
        out += chunk
        if marker in out:
            break
    return out


def test_spawn_read_exit_code():
    shell = os.environ.get("COMSPEC", "cmd.exe")
    pty = WeztermPseudoTerminal([shell, "/c", "echo HELLO_WZ"])
    try:
        assert pty.get_type() == "wezterm"
        assert pty.get_child_pid() is not None
        out = _read_until(pty, b"HELLO_WZ")
        assert b"HELLO_WZ" in out, out[:300]

        code = None
        deadline = time.time() + 5
        while time.time() < deadline:
            code = pty.get_exit_code()
            if code is not None:
                break
            time.sleep(0.1)
        assert code == 0, f"exit code={code}"
    finally:
        pty.close()


def test_resize_and_write():
    shell = os.environ.get("COMSPEC", "cmd.exe")
    # 交互式 cmd 会话：验证写入与 resize
    pty = WeztermPseudoTerminal([shell], cols=40, rows=10)
    try:
        pty.write(b"echo HI_AFTER_WRITE\r\n")
        out = _read_until(pty, b"HI_AFTER_WRITE", timeout=8.0)
        assert b"HI_AFTER_WRITE" in out, out[:300]
        pty.resize(80, 24)
        assert pty.pty.get_size() == (80, 24)
    finally:
        pty.close()
