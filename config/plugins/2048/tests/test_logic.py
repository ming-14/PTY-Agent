"""logic 模块单元测试。

运行：python -m unittest discover -s tests -v
或安装后：python -m pytest
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from game2048 import logic  # noqa: E402


class TestInit(unittest.TestCase):
    def test_initial_two_tiles(self):
        game = logic.Game(seed=0)
        values = [v for row in game.board for v in row if v != 0]
        self.assertEqual(len(values), logic.START_TILES)
        for v in values:
            self.assertIn(v, (2, 4))

    def test_board_shape(self):
        game = logic.Game(seed=0)
        self.assertEqual(len(game.board), logic.SIZE)
        for row in game.board:
            self.assertEqual(len(row), logic.SIZE)

    def test_invalid_size(self):
        with self.assertRaises(ValueError):
            logic.Game(size=1)

    def test_initial_status_playing(self):
        self.assertEqual(logic.Game(seed=0).status(), logic.PLAYING)


class TestSlide(unittest.TestCase):
    def _slide(self, line):
        return logic._slide(line, 4)[0]

    def test_merge_pair(self):
        self.assertEqual(self._slide([2, 2, 0, 0]), [4, 0, 0, 0])

    def test_merge_once_per_tile(self):
        self.assertEqual(self._slide([2, 2, 2, 2]), [4, 4, 0, 0])

    def test_merge_uneven(self):
        self.assertEqual(self._slide([2, 2, 4, 0]), [4, 4, 0, 0])

    def test_triple_merge(self):
        self.assertEqual(self._slide([2, 2, 2, 4]), [4, 2, 4, 0])

    def test_no_change(self):
        self.assertEqual(self._slide([4, 0, 0, 0]), [4, 0, 0, 0])

    def test_gain(self):
        _, gain, _ = logic._slide([2, 2, 2, 2], 4)
        self.assertEqual(gain, 8)

    def test_changed_flag(self):
        _, _, changed = logic._slide([4, 0, 0, 0], 4)
        self.assertFalse(changed)
        _, _, changed = logic._slide([2, 2, 0, 0], 4)
        self.assertTrue(changed)


class TestMove(unittest.TestCase):
    def test_move_left_merges(self):
        game = logic.Game(seed=1)
        game.board = [
            [2, 2, 0, 0],
            [2, 2, 2, 2],
            [4, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        game.score = 0
        moved = game.move(logic.LEFT)
        self.assertTrue(moved)
        # 第 0 行 [2,2]->[4,...]，第 1 行 [2,2,2,2]->[4,4,..]，第 2 行不变
        self.assertEqual(game.board[0][0], 4)
        self.assertEqual(game.board[1][0], 4)
        self.assertEqual(game.board[1][1], 4)
        self.assertEqual(game.board[2][0], 4)
        self.assertEqual(game.score, 12)

    def test_move_right(self):
        game = logic.Game(seed=2)
        game.board = [
            [2, 2, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        game.move(logic.RIGHT)
        self.assertEqual(game.board[0][3], 4)

    def test_move_up(self):
        game = logic.Game(seed=3)
        game.board = [
            [2, 0, 0, 0],
            [2, 0, 0, 0],
            [4, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        game.score = 0
        game.move(logic.UP)
        self.assertEqual(game.board[0][0], 4)
        self.assertEqual(game.board[1][0], 4)
        self.assertEqual(game.score, 4)

    def test_move_down(self):
        game = logic.Game(seed=4)
        game.board = [
            [2, 0, 0, 0],
            [2, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        game.move(logic.DOWN)
        self.assertEqual(game.board[3][0], 4)

    def test_spawn_after_valid_move(self):
        game = logic.Game(seed=5)
        game.board = [
            [0, 0, 2, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        self.assertTrue(game.move(logic.LEFT))
        nonzero = [v for row in game.board for v in row if v != 0]
        self.assertEqual(len(nonzero), 2)  # 原有 1 块 + 新生成 1 块

    def test_no_move_when_blocked(self):
        game = logic.Game(seed=6)
        blocked = [
            [2, 4, 2, 4],
            [4, 2, 4, 2],
            [2, 4, 2, 4],
            [4, 2, 4, 2],
        ]
        game.board = [list(row) for row in blocked]
        game.score = 0
        self.assertFalse(game.move(logic.LEFT))
        self.assertEqual(game.board, blocked)  # 棋盘不变
        self.assertEqual(game.score, 0)        # 未生成新块
        self.assertEqual(game.status(), logic.LOST)

    def test_invalid_direction(self):
        game = logic.Game(seed=7)
        with self.assertRaises(ValueError):
            game.move("north")


class TestStatus(unittest.TestCase):
    def test_win_detection(self):
        game = logic.Game(seed=8)
        game.board[0][0] = logic.WIN_VALUE
        self.assertEqual(game.status(), logic.WON)

    def test_lost_detection(self):
        game = logic.Game(seed=9)
        game.board = [
            [2, 4, 2, 4],
            [4, 2, 4, 2],
            [2, 4, 2, 4],
            [4, 2, 4, 2],
        ]
        self.assertEqual(game.status(), logic.LOST)


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_game(self):
        g1 = logic.Game(seed=42)
        g2 = logic.Game(seed=42)
        self.assertEqual(g1.board, g2.board)
        for direction in (logic.LEFT, logic.UP, logic.RIGHT, logic.DOWN):
            g1.move(direction)
            g2.move(direction)
        self.assertEqual(g1.board, g2.board)
        self.assertEqual(g1.score, g2.score)


class TestPlayability(unittest.TestCase):
    def test_random_game_terminates_cleanly(self):
        """随机打 200 步，确保不抛异常、分数非负。"""
        import random

        game = logic.Game(seed=1234)
        for _ in range(200):
            if game.status() == logic.LOST:
                break
            game.move(random.choice(logic.DIRECTIONS))
        self.assertGreaterEqual(game.score, 0)
        self.assertIn(game.status(), (logic.PLAYING, logic.WON, logic.LOST))


class TestWinFlag(unittest.TestCase):
    def test_just_win_set_on_merge(self):
        game = logic.Game(seed=11)
        half = logic.WIN_VALUE // 2  # 两个 half 合并恰为 WIN_VALUE
        game.board = [
            [half, half, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        self.assertEqual(game.status(), logic.PLAYING)
        game.move(logic.LEFT)
        self.assertTrue(game.just_win)

    def test_just_win_false_when_no_new_win(self):
        game = logic.Game(seed=12)
        game.move(logic.LEFT)
        self.assertFalse(game.just_win)

    def test_just_win_resets_after_already_won(self):
        game = logic.Game(seed=13)
        game.board[0][0] = logic.WIN_VALUE
        game.just_win = False
        game.move(logic.RIGHT)
        self.assertFalse(game.just_win)


if __name__ == "__main__":
    unittest.main()
