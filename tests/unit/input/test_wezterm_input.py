"""input/wezterm_input 单元测试 — wezterm-py 模式感知输入编码

验证编码器模式感知行为（应用光标模式/修饰键/鼠标上报），
以及 TerminalScreen.emulator 暴露与共享实例。
"""

import pytest

from src.input.wezterm_input import WeztermInputEncoder, MOD_SHIFT, MOD_CTRL, MOD_ALT
from src.terminal.screen import TerminalScreen
from src.terminal.backends import WeztermBackend, _HAS_WEZTERM

pytestmark = pytest.mark.skipif(not _HAS_WEZTERM, reason="wezterm-py 不可用")


def _make_encoder():
    term = WeztermBackend(40, 10).emulator
    return WeztermInputEncoder(term), term


def test_available():
    enc, _ = _make_encoder()
    assert enc.available is True
    assert WeztermInputEncoder(None).available is False


def test_key_down_normal_and_app_cursor():
    enc, term = _make_encoder()
    # 普通模式方向键 → ESC [ A
    assert enc.key_down("Up", 0) == b"\x1b[A"
    # 应用光标模式（DECCKM）方向键 → ESC O A
    term.feed(b"\x1b[?1h")
    assert enc.key_down("Up", 0) == b"\x1bOA"


def test_key_down_char_and_shift():
    enc, _ = _make_encoder()
    assert enc.key_down("a", 0) == b"a"
    # Shift+a → 大写（wezterm 编码处理）
    assert enc.key_down("a", MOD_SHIFT) == b"A"


def test_key_down_ctrl():
    enc, _ = _make_encoder()
    assert enc.key_down("c", MOD_CTRL) == b"\x03"
    assert enc.key_down("m", MOD_CTRL) == b"\r"


def test_key_up():
    enc, _ = _make_encoder()
    # 普通模式 keyup 通常为空（非 win32 键盘协议）
    assert enc.key_up("a", 0) == b""


def test_mouse_no_reporting_empty():
    enc, _ = _make_encoder()
    # 未启用鼠标上报 → 编码为空（不写入）
    assert enc.mouse(5, 3, "press", "left", 0) == b""


def test_mouse_sgr_reporting():
    enc, term = _make_encoder()
    term.feed(b"\x1b[?1000h\x1b[?1006h")
    # x=5,y=3 0-based → SGR 1006 1-based (6,4)
    enc_bytes = enc.mouse(5, 3, "press", "left", 0)
    assert enc_bytes.startswith(b"\x1b[<0;6;4") and enc_bytes.endswith(b"M"), enc_bytes
    rel = enc.mouse(5, 3, "release", "left", 0)
    assert rel.endswith(b"m"), rel


def test_encoder_shares_screen_model_state():
    """输入编码与终端模型共享同一实例：feed 后编码器感知模式变化"""
    screen = TerminalScreen(cols=40, rows=10)
    assert screen.emulator is not None
    enc = WeztermInputEncoder(screen.emulator)
    # 屏幕模型 feed 应用光标模式 → 同一实例的方向键编码变化
    screen.feed(b"\x1b[?1h")
    assert enc.key_down("Up", 0) == b"\x1bOA"
    # 正常 feed 文本后编码普通字符不受影响
    screen.feed(b"hello")
    assert enc.key_down("h", 0) == b"h"
