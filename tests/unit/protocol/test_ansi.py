"""protocol/ansi.py 单元测试"""

import pytest

from src.protocol.ansi import strip_ansi


class TestStripAnsi:
    def test_empty(self):
        assert strip_ansi("") == ""

    def test_no_ansi(self):
        assert strip_ansi("Hello, World!") == "Hello, World!"

    def test_csi_color(self):
        assert strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_keep_cursor(self):
        assert strip_ansi("\x1b[2J\x1b[Hclear") == "\x1b[2J\x1b[Hclear"

    def test_keep_line_erase(self):
        assert strip_ansi("line1\x1b[2Kline2") == "line1\x1b[2Kline2"

    def test_osc_bell(self):
        assert strip_ansi("\x1b]0;title\x07content") == "content"

    def test_osc_string_terminator(self):
        assert strip_ansi("\x1b]0;title\x1b\\content") == "content"

    def test_mixed(self):
        assert strip_ansi("\x1b[1m\x1b[32mBold Green\x1b[0m normal") == "Bold Green normal"

    def test_complex_csi_preserved(self):
        assert strip_ansi("\x1b[?25l\x1b[?25h") == "\x1b[?25l\x1b[?25h"

    def test_multiple_sgr(self):
        assert strip_ansi("\x1b[1;31;4mtext\x1b[0m") == "text"
