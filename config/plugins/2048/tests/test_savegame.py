"""savegame 模块单元测试：base64 存档编解码与配置文件读写。

运行：python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from game2048 import logic, savegame  # noqa: E402


class TestEncodeDecode(unittest.TestCase):
    def test_roundtrip(self):
        board = [[2, 4, 0, 0], [0, 0, 0, 0], [0, 8, 8, 0], [2, 0, 0, 4]]
        text = savegame.encode(board, 42)
        self.assertIsInstance(text, str)
        decoded = savegame.decode(text)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded, (board, 42))

    def test_decode_invalid_base64(self):
        self.assertIsNone(savegame.decode("!!!not-base64!!!"))

    def test_decode_invalid_json(self):
        import base64
        self.assertIsNone(savegame.decode(base64.b64encode(b"not json").decode()))

    def test_decode_wrong_shape(self):
        import base64, json
        for payload in (
            {"board": [[2]], "score": 0},          # 尺寸不符
            {"board": "x", "score": 0},            # board 非列表
            {"board": [[0] * 4 for _ in range(4)], "score": 1.5},  # score 非整数
            {"board": [[0] * 4 for _ in range(4)], "score": -1},   # 负分数
            {"board": [[0, 0, 0, -2] for _ in range(4)], "score": 0},  # 负格子
            {"board": [["a"] * 4 for _ in range(4)], "score": 0},  # 非整数格子
        ):
            text = base64.b64encode(json.dumps(payload).encode()).decode()
            self.assertIsNone(savegame.decode(text), "应拒绝: %r" % payload)


class TestFileIO(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(savegame, "_ROOT", self._tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _path(self):
        return os.path.join(self._tmp.name, savegame._FILE_NAME)

    def test_save_load_roundtrip(self):
        board = [[2, 4, 8, 16], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
        savegame.save(board, 64)
        self.assertTrue(os.path.isfile(self._path()))
        state = savegame.load()
        self.assertEqual(state, (board, 64))

    def test_load_missing_file_returns_none(self):
        self.assertIsNone(savegame.load())

    def test_load_corrupted_state_returns_none(self):
        with open(self._path(), "w", encoding="utf-8") as f:
            f.write("[game]\nbest = 10\nstate = !!!broken!!!\n")
        self.assertIsNone(savegame.load())
        # 其他配置键不受影响（clear 后可继续读 best）
        savegame.clear()
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(self._path(), encoding="utf-8")
        self.assertEqual(cfg.getint("game", "best"), 10)

    def test_clear_removes_state_keeps_others(self):
        savegame.save([[0] * 4 for _ in range(4)], 8)
        with open(self._path(), "a", encoding="utf-8") as f:
            pass
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(self._path(), encoding="utf-8")
        if not cfg.has_option("game", "win_value"):
            cfg.set("game", "win_value", "32")
            with open(self._path(), "w", encoding="utf-8") as f:
                cfg.write(f)
        savegame.clear()
        self.assertIsNone(savegame.load())
        cfg2 = configparser.ConfigParser()
        cfg2.read(self._path(), encoding="utf-8")
        self.assertFalse(cfg2.has_option("game", savegame._STATE_KEY))
        self.assertEqual(cfg2.getint("game", "win_value"), 32)

    def test_save_keeps_existing_best(self):
        with open(self._path(), "w", encoding="utf-8") as f:
            f.write("[game]\nbest = 3088\nwin_value = 32\n")
        savegame.save([[2] * 4 for _ in range(4)], 100)
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(self._path(), encoding="utf-8")
        self.assertEqual(cfg.getint("game", "best"), 3088)
        self.assertEqual(cfg.getint("game", "win_value"), 32)


class TestGameFromState(unittest.TestCase):
    def test_from_state_restores(self):
        board = [[2, 4, 8, 16], [0, 0, 0, 0], [0, 0, 0, 0], [32, 0, 0, 0]]
        game = logic.Game.from_state(board, 100, win_value=32)
        self.assertEqual(game.board, board)
        self.assertEqual(game.score, 100)
        self.assertEqual(game.win_value, 32)
        self.assertEqual(game.status(), logic.WON)

    def test_from_state_copies_board(self):
        board = [[2] * 4 for _ in range(4)]
        game = logic.Game.from_state(board, 0)
        board[0][0] = 999  # 修改外部数据不影响恢复的棋盘
        self.assertEqual(game.board[0][0], 2)

    def test_from_state_move_works(self):
        """恢复后的棋盘可正常移动/合并"""
        board = [[2, 2, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
        game = logic.Game.from_state(board, 0)
        self.assertTrue(game.move(logic.LEFT))
        self.assertEqual(game.board[0][0], 4)
        self.assertEqual(game.score, 4)


if __name__ == "__main__":
    unittest.main()
