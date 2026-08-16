"""client/config_manager.py 单元测试"""

import pytest

from src.client.config_manager import ConfigManager, _format_value


@pytest.fixture(autouse=True)
def _isolate_persistent_defaults(monkeypatch):
    """隔离本机 ~/.pty-agent/client_defaults.json（set-default 持久化）

    未隔离时用户机器上的持久化默认值会覆盖内置默认，导致断言不稳定。
    """
    monkeypatch.setattr(
        "src.client.config_manager.load_persistent_defaults", lambda: {}
    )


class TestConfigManagerGet:
    def test_get_default_timeout(self):
        cfg = ConfigManager()
        assert cfg.get("timeout") == 120.0

    def test_get_default_newline(self):
        cfg = ConfigManager()
        assert cfg.get("newline") is False

    def test_get_default_keep_ansi(self):
        cfg = ConfigManager()
        assert cfg.get("keep_ansi") is False

    def test_get_default_debug(self):
        cfg = ConfigManager()
        assert cfg.get("debug") is False

    def test_get_default_send_eol(self):
        cfg = ConfigManager()
        assert cfg.get("send_eol") == "\r"

    def test_get_unknown_key_returns_none(self):
        cfg = ConfigManager()
        assert cfg.get("nonexistent") is None

    def test_get_with_overrides(self):
        cfg = ConfigManager(overrides={"timeout": 30.0})
        assert cfg.get("timeout") == 30.0


class TestConfigManagerSet:
    def test_set_timeout(self):
        cfg = ConfigManager()
        cfg.set("timeout", "60")
        assert cfg.get("timeout") == 60.0

    def test_set_newline_bool(self):
        cfg = ConfigManager()
        cfg.set("newline", True)
        assert cfg.get("newline") is True

    def test_set_keep_ansi_on_off(self):
        cfg = ConfigManager()
        cfg.set("keep_ansi", "on")
        assert cfg.get("keep_ansi") is True
        cfg.set("keep_ansi", "off")
        assert cfg.get("keep_ansi") is False

    def test_set_send_eol_by_name(self):
        cfg = ConfigManager()
        cfg.set("send_eol", "cr")
        assert cfg.get("send_eol") == "\r"
        cfg.set("send_eol", "crlf")
        assert cfg.get("send_eol") == "\r\n"
        cfg.set("send_eol", "lf")
        assert cfg.get("send_eol") == "\n"
        cfg.set("send_eol", "none")
        assert cfg.get("send_eol") == ""

    def test_set_send_eol_direct(self):
        cfg = ConfigManager()
        cfg.set("send_eol", "\r")
        assert cfg.get("send_eol") == "\r"

    def test_set_invalid_key_raises(self):
        cfg = ConfigManager()
        with pytest.raises(ValueError, match="Unknown config key"):
            cfg.set("nonexistent_key", "value")

    def test_set_invalid_send_eol_raises(self):
        cfg = ConfigManager()
        with pytest.raises(ValueError, match="Invalid send-eol value"):
            cfg.set("send_eol", "invalid_value")

    def test_set_response_format_stream(self):
        cfg = ConfigManager()
        cfg.set("response_format", "stream")
        assert cfg.get("response_format") == "stream"

    def test_set_response_format_svg(self):
        cfg = ConfigManager()
        cfg.set("response_format", "svg")
        assert cfg.get("response_format") == "svg"

    def test_set_response_format_invalid(self):
        cfg = ConfigManager()
        with pytest.raises(ValueError, match="Invalid response-format value"):
            cfg.set("response_format", "html")

    def test_set_svg_compression_level_0(self):
        cfg = ConfigManager()
        cfg.set("svg_compression_level", 0)
        assert cfg.get("svg_compression_level") == 0

    def test_set_svg_compression_level_2(self):
        cfg = ConfigManager()
        cfg.set("svg_compression_level", 2)
        assert cfg.get("svg_compression_level") == 2

    def test_set_svg_compression_level_invalid(self):
        cfg = ConfigManager()
        with pytest.raises(ValueError, match="Invalid svg-compression-level value"):
            cfg.set("svg_compression_level", 5)

    def test_set_svg_compression_level_string(self):
        cfg = ConfigManager()
        cfg.set("svg_compression_level", "1")
        assert cfg.get("svg_compression_level") == 1

    def test_get_default_response_format(self):
        cfg = ConfigManager()
        assert cfg.get("response_format") == "stream"

    def test_get_default_svg_compression_level(self):
        cfg = ConfigManager()
        assert cfg.get("svg_compression_level") == 1


class TestConfigManagerGetAll:
    def test_get_all_returns_dict(self):
        cfg = ConfigManager()
        all_cfg = cfg.get_all()
        assert isinstance(all_cfg, dict)
        assert "timeout" in all_cfg
        assert "newline" in all_cfg

    def test_get_all_includes_overrides(self):
        cfg = ConfigManager(overrides={"timeout": 30.0})
        all_cfg = cfg.get_all()
        assert all_cfg["timeout"] == 30.0


class TestConfigManagerShow:
    def test_show_all(self):
        cfg = ConfigManager()
        text = cfg.show()
        assert "timeout" in text
        assert "newline" in text

    def test_show_single_key(self):
        cfg = ConfigManager()
        text = cfg.show("timeout")
        assert "timeout" in text
        assert "120" in text

    def test_show_unknown_key(self):
        cfg = ConfigManager()
        text = cfg.show("nonexistent")
        assert "未知" in text


class TestFormatValue:
    def test_bool_true(self):
        assert _format_value(True) == "on"

    def test_bool_false(self):
        assert _format_value(False) == "off"

    def test_none(self):
        assert _format_value(None) == "(未设置)"

    def test_lf(self):
        assert _format_value("\n") == "lf (\\n)"

    def test_crlf(self):
        assert _format_value("\r\n") == "crlf (\\r\\n)"

    def test_cr(self):
        assert _format_value("\r") == "cr (\\r)"

    def test_empty_string(self):
        assert _format_value("") == "none (不追加)"

    def test_number(self):
        assert _format_value(42) == "42"
