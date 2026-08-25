"""highscores 配置文件（.game2048）读写单元测试。

所有用例使用临时目录（通过替换模块级 _ROOT），不会触碰项目根目录的真实配置。
运行：python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from game2048 import highscores, logic  # noqa: E402


class _ConfigTestCase(unittest.TestCase):
    """把配置文件路径重定向到临时目录的基类。

    同时屏蔽包目录中的旧版最高分文件，保证测试不依赖真实项目状态。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root_patcher = mock.patch.object(highscores, "_ROOT", self._tmp.name)
        self._root_patcher.start()
        self._legacy_patcher = mock.patch.object(
            highscores, "_legacy_package_path",
            return_value=os.path.join(self._tmp.name, "no_such_legacy_file"))
        self._legacy_patcher.start()
        self.addCleanup(self._legacy_patcher.stop)
        self.addCleanup(self._root_patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _path(self) -> str:
        return os.path.join(self._tmp.name, highscores._FILE_NAME)

    def _write_raw(self, text: str) -> None:
        with open(self._path(), "w", encoding="utf-8") as f:
            f.write(text)


class TestEnsure(_ConfigTestCase):
    def test_creates_file_with_defaults(self):
        self.assertFalse(os.path.exists(self._path()))
        highscores.ensure()
        self.assertTrue(os.path.exists(self._path()))
        with open(self._path(), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[game]", content)
        self.assertIn("best = 0", content)
        self.assertIn("win_value = {}".format(logic.WIN_VALUE), content)

    def test_existing_file_is_kept(self):
        highscores.ensure()
        with open(self._path(), "a", encoding="utf-8") as f:
            f.write("# manual note\n")
        highscores.ensure()
        with open(self._path(), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("# manual note", content)

    def test_migrates_legacy_best_file(self):
        """旧版 .game2048_best 的数值应迁移为新配置的初始 best。"""
        legacy = os.path.join(self._tmp.name, highscores._LEGACY_FILE_NAME)
        with open(legacy, "w", encoding="utf-8") as f:
            f.write("620")
        highscores.ensure()
        self.assertEqual(highscores.best(), 620)


class TestBest(_ConfigTestCase):
    def test_missing_file_returns_zero(self):
        self.assertEqual(highscores.best(), 0)

    def test_roundtrip(self):
        highscores.save(620)
        self.assertEqual(highscores.best(), 620)

    def test_negative_clamped_to_zero(self):
        highscores.save(-5)
        self.assertEqual(highscores.best(), 0)

    def test_broken_value_returns_zero(self):
        self._write_raw("[game]\nbest = oops\n")
        self.assertEqual(highscores.best(), 0)


class TestWinValue(_ConfigTestCase):
    def test_missing_file_returns_default(self):
        self.assertEqual(highscores.win_value(), logic.WIN_VALUE)

    def test_custom_value(self):
        self._write_raw("[game]\nwin_value = 32\n")
        self.assertEqual(highscores.win_value(), 32)

    def test_broken_value_falls_back_to_default(self):
        self._write_raw("[game]\nwin_value = abc\n")
        self.assertEqual(highscores.win_value(), logic.WIN_VALUE)

    def test_small_value_falls_back_to_default(self):
        self._write_raw("[game]\nwin_value = 1\n")
        self.assertEqual(highscores.win_value(), logic.WIN_VALUE)

    def test_save_best_keeps_win_value(self):
        """保存 best 不得覆盖用户配置的 win_value。"""
        self._write_raw("[game]\nwin_value = 64\n")
        highscores.save(100)
        self.assertEqual(highscores.best(), 100)
        self.assertEqual(highscores.win_value(), 64)


class TestGameWithWinValue(unittest.TestCase):
    """logic.Game 应支持配置传入的 win_value。"""

    def test_custom_win_value_triggers_won(self):
        game = logic.Game(seed=1, win_value=32)
        self.assertEqual(game.status(), logic.PLAYING)
        game.board[0][0] = 32
        self.assertEqual(game.status(), logic.WON)

    def test_default_win_value_is_module_default(self):
        game = logic.Game(seed=1)
        self.assertEqual(game.win_value, logic.WIN_VALUE)
        self.assertEqual(game.win_value, 2048)

    def test_invalid_win_value_rejected(self):
        with self.assertRaises(ValueError):
            logic.Game(seed=1, win_value=1)


if __name__ == "__main__":
    unittest.main()
