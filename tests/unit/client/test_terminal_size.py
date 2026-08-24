"""单元测试：终端尺寸配置与传递"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.client.config_manager import ConfigManager, parse_terminal_size
from src.config.common import DEFAULT_COLS, DEFAULT_ROWS
from src.config.default_keys import DEFAULT_VALUES as _DEFAULTS

# spawn 探测用命令：Windows 用 cmd wrapper，Unix 直接 echo
_SHELL_CMD = ["cmd", "/c", "echo", "hi"] if sys.platform == "win32" else ["echo", "hi"]


@pytest.fixture(autouse=True)
def _isolate_persistent_defaults(monkeypatch):
    """set-default 全局默认存于守护进程内存（不写文件）；单测无需隔离"""


class TestParseTerminalSize:
    def test_standard(self):
        assert parse_terminal_size("80x24") == (80, 24)

    def test_wide(self):
        assert parse_terminal_size("120x40") == (120, 40)

    def test_large(self):
        assert parse_terminal_size("300x100") == (300, 100)

    def test_minimal(self):
        assert parse_terminal_size("20x5") == (20, 5)

    def test_unicode_x(self):
        assert parse_terminal_size("120×40") == (120, 40)

    def test_uppercase_X(self):
        assert parse_terminal_size("120X40") == (120, 40)

    def test_invalid_format_no_x(self):
        with pytest.raises(ValueError, match="expected WxH"):
            parse_terminal_size("120")

    def test_invalid_format_extra_x(self):
        with pytest.raises(ValueError, match="expected WxH"):
            parse_terminal_size("120x40x2")

    def test_invalid_non_numeric(self):
        with pytest.raises(ValueError):
            parse_terminal_size("abcx40")

    def test_cols_too_small(self):
        with pytest.raises(ValueError, match="out of range"):
            parse_terminal_size("10x24")

    def test_cols_too_large(self):
        with pytest.raises(ValueError, match="out of range"):
            parse_terminal_size("600x24")

    def test_rows_too_small(self):
        with pytest.raises(ValueError, match="out of range"):
            parse_terminal_size("80x2")

    def test_rows_too_large(self):
        with pytest.raises(ValueError, match="out of range"):
            parse_terminal_size("80x300")


class TestConfigManagerTerminalSize:
    def test_default_value(self):
        cm = ConfigManager()
        assert cm.get("terminal_size") == "80x24"

    def test_set_valid(self):
        cm = ConfigManager()
        cm.set("terminal_size", "120x40")
        assert cm.get("terminal_size") == "120x40"

    def test_set_unicode_x(self):
        cm = ConfigManager()
        cm.set("terminal_size", "100×30")
        assert cm.get("terminal_size") == "100x30"

    def test_set_invalid_format(self):
        cm = ConfigManager()
        with pytest.raises(ValueError, match="expected WxH"):
            cm.set("terminal_size", "invalid")

    def test_set_out_of_range(self):
        cm = ConfigManager()
        with pytest.raises(ValueError):
            cm.set("terminal_size", "10x5")

    def test_show_includes_terminal_size(self):
        cm = ConfigManager()
        text = cm.show()
        assert "terminal_size" in text

    def test_show_specific_key(self):
        cm = ConfigManager()
        cm.set("terminal_size", "120x40")
        text = cm.show("terminal_size")
        assert "120x40" in text

    def test_defaults_dict_includes_terminal_size(self):
        assert "terminal_size" in _DEFAULTS
        assert _DEFAULTS["terminal_size"] == "80x24"


class TestConfigDefaults:
    def test_default_cols(self):
        assert DEFAULT_COLS == 80

    def test_default_rows(self):
        assert DEFAULT_ROWS == 24

    def test_defaults_are_int(self):
        assert isinstance(DEFAULT_COLS, int)
        assert isinstance(DEFAULT_ROWS, int)


class TestSessionTerminalSize:
    def test_session_default_size(self):
        from src.session.session import Session
        s = Session("test", ["echo", "hi"])
        assert s.cols == DEFAULT_COLS
        assert s.rows == DEFAULT_ROWS

    def test_session_custom_size(self):
        from src.session.session import Session
        s = Session("test", ["echo", "hi"], cols=120, rows=40)
        assert s.cols == 120
        assert s.rows == 40

    def test_session_none_uses_default(self):
        from src.session.session import Session
        s = Session("test", ["echo", "hi"], cols=None, rows=None)
        assert s.cols == DEFAULT_COLS
        assert s.rows == DEFAULT_ROWS

    def test_session_only_cols(self):
        from src.session.session import Session
        s = Session("test", ["echo", "hi"], cols=120)
        assert s.cols == 120
        assert s.rows == DEFAULT_ROWS

    def test_session_resize_not_forced(self):
        from src.session.session import Session
        s = Session("test", ["echo", "hi"], cols=120, rows=40)
        assert s.cols == 120
        assert s.rows == 40


class TestManagerTerminalSize:
    def test_manager_passes_cols_rows(self):
        from unittest.mock import patch
        from src.session.manager import SessionManager
        mgr = SessionManager()
        with patch.object(mgr, '_on_session_ended'):
            s = mgr.create_session("t1", _SHELL_CMD, cols=100, rows=30)
            assert s.cols == 100
            assert s.rows == 30

    def test_manager_default_cols_rows(self):
        from unittest.mock import patch
        from src.session.manager import SessionManager
        mgr = SessionManager()
        with patch.object(mgr, '_on_session_ended'):
            s = mgr.create_session("t2", _SHELL_CMD)
            assert s.cols == DEFAULT_COLS
            assert s.rows == DEFAULT_ROWS

    def test_manager_none_cols_rows(self):
        from unittest.mock import patch
        from src.session.manager import SessionManager
        mgr = SessionManager()
        with patch.object(mgr, '_on_session_ended'):
            s = mgr.create_session("t3", _SHELL_CMD, cols=None, rows=None)
            assert s.cols == DEFAULT_COLS
            assert s.rows == DEFAULT_ROWS
