"""app.main 参数行为测试：--new 清存档，simple 切换呈现层并跳过奖杯。

运行：python -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from game2048 import app  # noqa: E402


class TestMainArgs(unittest.TestCase):
    """测试 main() 参数：--new 清空存档，simple 切换呈现层。"""

    def _run_main(self, argv, run=None):
        """以给定 argv 运行 app.main，mock 掉交互循环与终端 IO。

        run: 外部传入的 _run mock（验证调用）；缺省时用匿名 mock 防死循环。
        """
        run = run if run is not None else mock.Mock()
        with mock.patch.object(app.ui, "init"), \
                mock.patch.object(app.ui, "cleanup"), \
                mock.patch.object(app, "_run", run), \
                mock.patch("builtins.print"):
            app.main(argv)
        return run

    def test_new_clears_savegame(self):
        with mock.patch.object(app.savegame, "clear") as clear:
            self._run_main([app.NEW_ARG])
            clear.assert_called_once()

    def test_without_new_keeps_savegame(self):
        with mock.patch.object(app.savegame, "clear") as clear:
            self._run_main([])
            clear.assert_not_called()

    def test_new_with_simple_clears_savegame(self):
        with mock.patch.object(app.savegame, "clear") as clear:
            self._run_main([app.NEW_ARG, app.SIMPLE_ARG])
            clear.assert_called_once()

    def test_simple_sets_simple(self):
        with mock.patch.object(app.ui, "set_simple") as set_simple:
            self._run_main([app.SIMPLE_ARG])
            set_simple.assert_called_once_with(True)

    def test_normal_does_not_set_simple(self):
        with mock.patch.object(app.ui, "set_simple") as set_simple:
            self._run_main([])
            set_simple.assert_called_once_with(False)

    def test_always_runs(self):
        run = mock.Mock()
        self._run_main([app.SIMPLE_ARG], run=run)
        run.assert_called_once()
        run2 = mock.Mock()
        self._run_main([], run=run2)
        run2.assert_called_once()


if __name__ == "__main__":
    unittest.main()