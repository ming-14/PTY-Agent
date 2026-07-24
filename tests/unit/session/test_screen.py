"""session/screen.py 单元测试 — TerminalScreen"""

import pytest

from src.terminal.screen import TerminalScreen


class TestTerminalScreenInit:
    def test_default_size(self):
        screen = TerminalScreen()
        assert screen._cols == 80
        assert screen._rows == 24

    def test_custom_size(self):
        screen = TerminalScreen(cols=120, rows=40)
        assert screen._cols == 120
        assert screen._rows == 40

    def test_available_when_pyte_installed(self):
        screen = TerminalScreen()
        try:
            import pyte
            assert screen.available is True
        except ImportError:
            assert screen.available is False


class TestTerminalScreenSnapshot:
    def test_empty_snapshot(self):
        screen = TerminalScreen()
        result = screen.snapshot()
        assert isinstance(result, str)

    def test_snapshot_after_feed(self):
        screen = TerminalScreen()
        screen.feed(b"Hello World")
        result = screen.snapshot()
        assert "Hello World" in result

    def test_snapshot_keep_ansi(self):
        screen = TerminalScreen()
        screen.feed(b"Hello")
        result = screen.snapshot(keep_ansi=True)
        assert isinstance(result, str)

    def test_snapshot_multiline(self):
        screen = TerminalScreen()
        screen.feed(b"Line1\nLine2\nLine3")
        result = screen.snapshot()
        assert "Line1" in result
        assert "Line2" in result
        assert "Line3" in result

    def test_snapshot_strips_trailing_empty_lines(self):
        screen = TerminalScreen()
        result = screen.snapshot()
        assert not result.endswith("\n\n")


class TestTerminalScreenFeed:
    def test_feed_increments_count(self):
        screen = TerminalScreen()
        initial_count = screen._feed_count
        screen.feed(b"test")
        assert screen._feed_count == initial_count + 1

    def test_feed_increments_bytes(self):
        screen = TerminalScreen()
        screen.feed(b"hello")
        assert screen._feed_bytes >= 5

    def test_feed_empty_data(self):
        screen = TerminalScreen()
        screen.feed(b"")
        assert screen._feed_count >= 1

    def test_feed_unicode(self):
        screen = TerminalScreen()
        screen.feed("你好世界".encode("utf-8"))
        result = screen.snapshot()
        assert "你好世界" in result


class TestTerminalScreenResize:
    def test_resize(self):
        screen = TerminalScreen()
        screen.resize(120, 40)
        assert screen._cols == 120
        assert screen._rows == 40


class TestTerminalScreenReset:
    def test_reset(self):
        screen = TerminalScreen()
        screen.feed(b"Hello")
        screen.reset()
        result = screen.snapshot()
        assert "Hello" not in result


class TestTerminalScreenDiagnostics:
    def test_diagnostics_returns_dict(self):
        screen = TerminalScreen()
        info = screen.diagnostics()
        assert isinstance(info, dict)
        assert "pyte_available" in info
        assert "feed_count" in info
        assert "feed_bytes" in info

    def test_diagnostics_after_feed(self):
        screen = TerminalScreen()
        screen.feed(b"test data")
        info = screen.diagnostics()
        assert info["feed_count"] >= 1
        assert info["feed_bytes"] >= 9


class TestTerminalScreenColorRendering:
    def test_ansi_color_fg(self):
        screen = TerminalScreen()
        screen.feed(b"\x1b[31mRed Text\x1b[0m")
        result = screen.snapshot(keep_ansi=True)
        assert "Red Text" in result
        assert "\x1b[" in result

    def test_ansi_bold(self):
        screen = TerminalScreen()
        screen.feed(b"\x1b[1mBold\x1b[0m")
        result = screen.snapshot(keep_ansi=True)
        assert "Bold" in result
