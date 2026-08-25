"""ui 渲染回归测试：奖杯浮层与成就柜叠加（glow 悬停）时不得输出裸 ANSI 转义。

运行：python -m unittest discover -s tests -v
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from game2048 import logic, sprites, ui  # noqa: E402

# 画布渲染使用的全部转义均为 CSI 样式序列（\x1b[ ... m）
_CSI_STYLE = re.compile(r"\x1b\[[0-9;]*m")


def count_broken_escapes(text: str) -> int:
    """统计不在完整 CSI 样式序列中的裸 ESC 数量。"""
    count = 0
    i = 0
    while True:
        j = text.find("\x1b", i)
        if j < 0:
            return count
        if not _CSI_STYLE.match(text, j):
            count += 1
        i = j + 1


class TestCabinetSprite(unittest.TestCase):
    def test_glow_rows_contain_no_ansi(self):
        """glow 模式返回的行内不得内嵌 ANSI 转义（应交给画布 style 上色）。"""
        rows, _, _ = sprites.cabinet(False, glow=True)
        for line in rows:
            self.assertNotIn("\x1b", line,
                             "cabinet 行内嵌 ANSI 会被画布当作字面字符逐格存放")

    def test_glow_does_not_change_dimensions(self):
        rows, w, h = sprites.cabinet(False, glow=True)
        rows2, w2, h2 = sprites.cabinet(False, glow=False)
        self.assertEqual((w, h), (w2, h2))
        self.assertEqual(len(rows), len(rows2))

    def test_all_rows_same_width(self):
        """柜子各行必须等宽（槽位行曾比柜体宽 2 列导致错位）。"""
        for filled in (False, True):
            rows, w, h = sprites.cabinet(filled)
            self.assertEqual(len(rows), h)
            for line in rows:
                self.assertEqual(len(line), w,
                                 "柜子第 {} 行宽度 {} != 柜体宽度 {}".format(
                                     rows.index(line), len(line), w))


class TestRenderOverlap(unittest.TestCase):
    def _game_with_win(self) -> "logic.Game":
        game = logic.Game(seed=1)
        game.board[0][0] = logic.WIN_VALUE
        return game

    def test_no_raw_escape_when_trophy_over_cabinet(self):
        """奖杯拖到橱柜上方（未松手，glow 悬停）渲染不得出现裸转义/乱码。"""
        game = self._game_with_win()
        trophy = ui.spawn_trophy(game)
        trophy["held"] = True
        cr, cc, _, _ = ui.cabinet_rect(game)
        # 奖杯压在橱柜首行左角：恰好覆盖旧版内嵌 GOLD 转义序列的位置
        trophy["row"] = cr - 2
        trophy["col"] = cc - 2
        out = ui._build_screen(game, logic.WON, 0, trophy, None, False, True)
        self.assertEqual(count_broken_escapes(out), 0)

    def test_no_raw_escape_when_trophy_partially_over_cabinet(self):
        """奖杯半压橱柜（只盖住边框中间）同样不得出现裸转义。"""
        game = self._game_with_win()
        trophy = ui.spawn_trophy(game)
        trophy["held"] = True
        cr, cc, _, _ = ui.cabinet_rect(game)
        trophy["row"] = cr - 3
        trophy["col"] = cc + 3
        out = ui._build_screen(game, logic.WON, 0, trophy, None, False, True)
        self.assertEqual(count_broken_escapes(out), 0)


class TestBoardText(unittest.TestCase):
    """简单模式纯文本输出：空位 '-'，数字以空格分隔，无任何其他元素。"""

    def _game(self, board):
        game = logic.Game(seed=0)
        game.board = [list(row) for row in board]
        return game

    def test_empty_board(self):
        text = ui.board_text(self._game([[0] * 4] * 4))
        self.assertEqual(text, "\n".join(["- - - -"] * 4))

    def test_full_board(self):
        board = [[2] * 4] * 4
        text = ui.board_text(self._game(board))
        self.assertEqual(text, "\n".join(["2 2 2 2"] * 4))

    def test_mixed_board(self):
        board = [
            [2, 0, 4, 0],
            [0, 0, 0, 0],
            [8, 16, 0, 32],
            [0, 0, 0, 0],
        ]
        text = ui.board_text(self._game(board))
        self.assertEqual(text, "\n".join([
            "2 - 4 -",
            "- - - -",
            "8 16 - 32",
            "- - - -",
        ]))

    def test_only_board_chars(self):
        """simple 文本只含数字/空格/'-'/换行，不含边框、提示、转义。"""
        text = ui.board_text(self._game([[2, 0, 4, 8]] * 4))
        allowed = set("0123456789 -\n")
        self.assertTrue(set(text) <= allowed, repr(text))


class TestSimplePresentation(unittest.TestCase):
    """simple 呈现模式：render() 只输出棋盘纯文本，忽略全部附加元素。"""

    def setUp(self):
        ui.set_simple(True)
        self.addCleanup(ui.set_simple, False)

    def test_simple_flag(self):
        ui.set_simple(True)
        self.assertTrue(ui.simple())
        ui.set_simple(False)
        self.assertFalse(ui.simple())

    def test_render_outputs_board_only(self):
        """simple 模式下 render() 输出纯棋盘文本，无得分/提示/奖杯/转义。"""
        import io
        import contextlib

        game = logic.Game(seed=1)
        game.board[0][0] = logic.WIN_VALUE
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ui.render(game, logic.WON, 999, {"row": 1, "col": 1}, 0, True, True)
        text = out.getvalue()
        # 清屏转义后紧跟棋盘文本；不含任何 ANSI 样式、不含得分/提示字符
        self.assertNotIn("\x1b[", text.replace("\x1b[2J\x1b[H", "", 1))
        self.assertNotIn("Score", text)
        self.assertNotIn("trophy", text.lower())
        allowed = set("0123456789 -\n")
        body = text.replace("\x1b[2J\x1b[H", "", 1).strip()
        self.assertTrue(set(body) <= allowed, repr(body))


if __name__ == "__main__":
    unittest.main()
