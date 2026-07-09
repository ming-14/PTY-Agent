"""协议层单元测试 — ansi 模块"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from src.protocol.ansi import strip_ansi


def test_strip_ansi_empty():
    """空字符串"""


    assert strip_ansi("") == ""


def test_strip_ansi_no_ansi():
    """无 ANSI 序列的普通文本"""

    text = "Hello, World!"
    assert strip_ansi(text) == text


def test_strip_ansi_csi_color():
    """CSI 颜色序列 (ESC [ ... m)"""

    result = strip_ansi("\x1b[31mred\x1b[0m")
    assert result == "red", f"got {result!r}"


def test_strip_ansi_keep_cursor():
    """光标控制/清屏序列应保留（不再过滤）"""

    result = strip_ansi("\x1b[2J\x1b[Hclear")
    assert result == "\x1b[2J\x1b[Hclear", f"got {result!r}"


def test_strip_ansi_keep_line_erase():
    """清行序列应保留"""

    result = strip_ansi("line1\x1b[2Kline2")
    assert result == "line1\x1b[2Kline2", f"got {result!r}"


def test_strip_ansi_osc():
    """OSC 序列 (ESC ] ... BEL/ST)"""

    result = strip_ansi("\x1b]0;title\x07content")
    assert result == "content", f"got {result!r}"

    result = strip_ansi("\x1b]0;title\x1b\\content")
    assert result == "content", f"got {result!r}"


def test_strip_ansi_keep_single_byte():
    """单字节 ESC 控制符应保留（不再过滤）"""

    result = strip_ansi("\x1bD\x1bM\x1breset")
    # 新行为：不再过滤单字节 ESC 控制符
    assert result == "\x1bD\x1bM\x1breset", f"got {result!r}"


def test_strip_ansi_mixed():
    """混合文本"""

    result = strip_ansi("\x1b[1m\x1b[32mBold Green\x1b[0m normal")
    assert result == "Bold Green normal", f"got {result!r}"


def test_strip_ansi_complex_csi():
    """复杂 CSI 参数——光标显示控制应保留（不以 m 结尾）"""

    result = strip_ansi("\x1b[?25l\x1b[?25h")
    # ?25l 和 ?25h 不以 m 结尾，不再过滤
    assert result == "\x1b[?25l\x1b[?25h", f"got {result!r}"


def run_all():
    """运行所有测试"""
    tests = [
        ("空字符串",                  test_strip_ansi_empty),
        ("无 ANSI 文本",              test_strip_ansi_no_ansi),
        ("CSI 颜色",                  test_strip_ansi_csi_color),
        ("光标/清屏保留",             test_strip_ansi_keep_cursor),
        ("清行保留",                  test_strip_ansi_keep_line_erase),
        ("OSC 序列",                  test_strip_ansi_osc),
        ("单字节 ESC 保留",           test_strip_ansi_keep_single_byte),
        ("混合文本",                  test_strip_ansi_mixed),
        ("复杂 CSI 保留",             test_strip_ansi_complex_csi),
    ]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  [PASS] {name}")
        except AssertionError as e:
            print(f"  [FAIL] {name}: {e}")
        except Exception as e:
            print(f"  [FAIL] {name}: 异常 {e}")
    total = len(tests)
    print(f"\n结果: {passed}/{total} 通过")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
