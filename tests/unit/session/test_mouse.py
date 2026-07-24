"""鼠标动作编码与屏幕坐标解析单元测试"""

import pytest

from src.input.mouse import (
    MouseActionEncoder,
    MouseError,
    Coord,
    MatchRegion,
    grep_screen,
)


class _MockScreen:
    """用于 grep 测试的最小屏幕 mock"""

    def __init__(self, lines):
        self.rows = len(lines)
        self._lines = lines

    def line_text(self, row):
        if 0 <= row < len(self._lines):
            return self._lines[row]
        return ""


class TestCoordAndRegion:
    def test_coord_as_dict(self):
        c = Coord(col=10, row=5)
        assert c.as_dict() == {"col": 10, "row": 5}

    def test_region_as_dict(self):
        r = MatchRegion(start=Coord(1, 2), end=Coord(3, 2))
        assert r.as_dict() == {"start": {"col": 1, "row": 2}, "end": {"col": 3, "row": 2}}


class TestMouseActionEncoderClick:
    def test_left_click(self):
        enc = MouseActionEncoder(80, 24)
        ops = enc.click(Coord(10, 5), "left", 1, [])
        assert len(ops) == 2
        assert ops[0]["type"] == "write"
        assert ops[0]["data"] == b"\x1b[<0;10;5M"
        assert ops[1]["data"] == b"\x1b[<3;10;5m"

    def test_right_click_with_modifiers(self):
        enc = MouseActionEncoder(80, 24)
        ops = enc.click(Coord(1, 1), "right", 1, ["ctrl", "shift"])
        # right=2, ctrl=16, shift=4 => 22; release=3 + modifiers => 23
        assert ops[0]["data"] == b"\x1b[<22;1;1M"
        assert ops[1]["data"] == b"\x1b[<23;1;1m"

    def test_double_click_has_delay(self):
        enc = MouseActionEncoder(80, 24)
        ops = enc.click(Coord(2, 2), "left", 2, [])
        assert len(ops) == 5  # press, release, sleep, press, release
        assert ops[2]["type"] == "sleep"
        assert ops[2]["duration"] == 0.05

    def test_invalid_button(self):
        enc = MouseActionEncoder(80, 24)
        with pytest.raises(MouseError):
            enc.click(Coord(1, 1), "unknown", 1, [])

    def test_invalid_count(self):
        enc = MouseActionEncoder(80, 24)
        with pytest.raises(MouseError):
            enc.click(Coord(1, 1), "left", 4, [])

    def test_out_of_range(self):
        enc = MouseActionEncoder(80, 24)
        with pytest.raises(MouseError):
            enc.click(Coord(81, 1), "left", 1, [])


class TestMouseActionEncoderHover:
    def test_hover(self):
        enc = MouseActionEncoder(80, 24)
        ops = enc.hover(Coord(5, 5), ["alt"])
        assert len(ops) == 1
        # release=3, alt=8, motion=32 => 43
        assert ops[0]["data"] == b"\x1b[<43;5;5m"


class TestMouseActionEncoderScroll:
    def test_scroll_up_twice(self):
        enc = MouseActionEncoder(80, 24)
        ops = enc.scroll(Coord(10, 10), "up", 2, [])
        assert len(ops) == 4
        assert ops[0]["data"] == b"\x1b[<4;10;10M"
        assert ops[1]["data"] == b"\x1b[<3;10;10m"
        assert ops[2]["data"] == b"\x1b[<4;10;10M"
        assert ops[3]["data"] == b"\x1b[<3;10;10m"

    def test_scroll_down_with_ctrl(self):
        enc = MouseActionEncoder(80, 24)
        ops = enc.scroll(Coord(1, 1), "down", 1, ["ctrl"])
        # scroll_down=5, ctrl=16 => 21
        assert ops[0]["data"] == b"\x1b[<21;1;1M"

    def test_invalid_direction(self):
        enc = MouseActionEncoder(80, 24)
        with pytest.raises(MouseError):
            enc.scroll(Coord(1, 1), "left", 1, [])


class TestMouseActionEncoderDrag:
    def test_horizontal_drag(self):
        enc = MouseActionEncoder(80, 24)
        ops = enc.drag(Coord(1, 1), Coord(3, 1), "left", [])
        # press at (1,1), motion at (2,1) (3,1), release at (3,1)
        assert ops[0]["data"] == b"\x1b[<0;1;1M"
        assert ops[1]["data"] == b"\x1b[<32;2;1M"  # motion
        assert ops[2]["data"] == b"\x1b[<32;3;1M"  # motion
        assert ops[3]["data"] == b"\x1b[<3;3;1m"

    def test_vertical_drag(self):
        enc = MouseActionEncoder(80, 24)
        ops = enc.drag(Coord(5, 1), Coord(5, 3), "right", [])
        assert len(ops) == 4
        assert ops[0]["data"] == b"\x1b[<2;5;1M"
        assert ops[-1]["data"] == b"\x1b[<3;5;3m"


class TestMouseActionEncoderPress:
    def test_long_press(self):
        enc = MouseActionEncoder(80, 24)
        ops = enc.press(Coord(4, 4), "middle", 2.0, ["shift"])
        assert len(ops) == 3
        # middle=1, shift=4 => 5
        assert ops[0]["data"] == b"\x1b[<5;4;4M"
        assert ops[1] == {"type": "sleep", "duration": 2.0}
        assert ops[2]["data"] == b"\x1b[<7;4;4m"

    def test_invalid_duration(self):
        enc = MouseActionEncoder(80, 24)
        with pytest.raises(MouseError):
            enc.press(Coord(1, 1), "left", 0, [])


class TestGrepScreen:
    def test_single_match(self):
        screen = _MockScreen(["hello world", "foo bar"])
        matches = grep_screen(screen, "world")
        assert len(matches) == 1
        # "world" is at 0-based [6:11) -> 1-based start=7, end=11
        assert matches[0].start == Coord(col=7, row=1)
        assert matches[0].end == Coord(col=11, row=1)

    def test_multiple_matches(self):
        screen = _MockScreen(["abc abc", "def"])
        matches = grep_screen(screen, "abc")
        assert len(matches) == 2
        assert matches[0].start == Coord(col=1, row=1)
        assert matches[1].start == Coord(col=5, row=1)

    def test_multiple_lines(self):
        screen = _MockScreen(["foo", "bar foo"])
        matches = grep_screen(screen, "foo")
        assert len(matches) == 2
        assert matches[0].start == Coord(col=1, row=1)
        assert matches[1].start == Coord(col=5, row=2)

    def test_no_match(self):
        screen = _MockScreen(["foo", "bar"])
        matches = grep_screen(screen, "baz")
        assert matches == []

    def test_invalid_regex(self):
        screen = _MockScreen(["foo"])
        with pytest.raises(MouseError):
            grep_screen(screen, "[invalid")
