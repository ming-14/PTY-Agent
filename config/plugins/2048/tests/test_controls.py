"""controls 模块字节解析单元测试。

运行：python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from game2048 import controls  # noqa: E402


def parse(buf: str):
    """按字符逐个喂给解析器，返回 (event, consumed)。"""
    return controls._parse_bytes(buf.encode("latin-1"))


class TestArrowKeys(unittest.TestCase):
    def test_arrows(self):
        self.assertEqual(parse("\x1b[A")[0], "up")
        self.assertEqual(parse("\x1b[B")[0], "down")
        self.assertEqual(parse("\x1b[C")[0], "right")
        self.assertEqual(parse("\x1b[D")[0], "left")

    def test_arrow_consumed_bytes(self):
        self.assertEqual(parse("\x1b[A")[1], 3)

    def test_other_csi_discarded(self):
        self.assertIsNone(parse("\x1b[1~")[0])  # Home
        self.assertIsNone(parse("\x1b[5~")[0])  # PageUp
        self.assertIsNone(parse("\x1b[Z")[0])   # Shift+Tab

    def test_incomplete_sequence(self):
        ev, consumed = parse("\x1b[")
        self.assertIsNone(ev)
        self.assertEqual(consumed, 0)  # 需继续读入


class TestPlainKeys(unittest.TestCase):
    def test_letters(self):
        self.assertEqual(parse("w")[0], "up")
        self.assertEqual(parse("a")[0], "left")
        self.assertEqual(parse("s")[0], "down")
        self.assertEqual(parse("d")[0], "right")
        self.assertEqual(parse("q")[0], "quit")
        self.assertEqual(parse("r")[0], "restart")

    def test_case_insensitive(self):
        self.assertEqual(parse("W")[0], "up")
        self.assertEqual(parse("Q")[0], "quit")

    def test_unknown(self):
        self.assertIsNone(parse("x")[0])


class TestSgrMouse(unittest.TestCase):
    def test_press(self):
        ev, consumed = parse("\x1b[<0;10;5M")
        self.assertEqual(ev, {"type": "mouse", "action": "press", "button": 0,
                              "x": 10, "y": 5})
        self.assertEqual(consumed, len("\x1b[<0;10;5M"))

    def test_release(self):
        ev, _ = parse("\x1b[<0;3;2m")
        self.assertEqual(ev["action"], "release")
        self.assertEqual((ev["x"], ev["y"]), (3, 2))

    def test_move(self):
        ev, _ = parse("\x1b[<32;8;6M")
        self.assertEqual(ev["action"], "move")

    def test_wheel_ignored(self):
        ev, _ = parse("\x1b[<64;1;1M")
        self.assertEqual(ev["action"], "wheel")

    def test_incomplete_sgr(self):
        ev, consumed = parse("\x1b[<0;10")
        self.assertIsNone(ev)
        self.assertEqual(consumed, 0)


class TestLegacyMouse(unittest.TestCase):
    def test_legacy_press(self):
        # ESC [ M Cb(0+32) Cx(5+32) Cy(5+32)
        ev, consumed = parse("\x1b[M %%" )
        self.assertEqual(ev["action"], "press")
        self.assertEqual((ev["x"], ev["y"]), (5, 5))
        self.assertEqual(consumed, 6)


class TestReadKeyBuffer(unittest.TestCase):
    def test_multiple_events_in_buffer(self):
        """一次读入多个事件，应逐个消费、互不干扰。"""
        controls._BUFFER.extend(b"w\x1b[A\x1b[<0;1;1M")
        self.assertEqual(controls.read_key(), "up")
        self.assertEqual(controls.read_key(), "up")
        ev = controls.read_key()
        self.assertEqual(ev["action"], "press")
        self.assertEqual(len(controls._BUFFER), 0)

    def tearDown(self):
        controls._BUFFER.clear()


if __name__ == "__main__":
    unittest.main()
