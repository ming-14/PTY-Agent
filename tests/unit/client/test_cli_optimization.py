"""ConfigManager 单元测试

测试客户端配置管理与配置解析。"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_persistent_defaults(monkeypatch):
    """隔离本机 ~/.pty-agent/client_defaults.json（set-default 持久化）"""
    monkeypatch.setattr(
        "src.client.config_manager.load_persistent_defaults", lambda: {}
    )


# ---- ConfigManager 测试 ----


class TestConfigManager:
    """配置管理器单元测试（纯内存模式，无文件持久化）"""

    @pytest.fixture
    def cfg(self):
        """创建 ConfigManager 实例"""
        from src.client.config_manager import ConfigManager

        return ConfigManager()

    def test_default_values(self, cfg):
        """测试默认配置值"""
        assert cfg.get("timeout") == 120.0
        assert cfg.get("newline") is False
        assert cfg.get("encoding") is None
        assert cfg.get("keep_ansi") is False
        assert cfg.get("debug") is False
        assert cfg.get("send_eol") == "\r"

    def test_set_and_get(self, cfg):
        """测试设置值并读取"""
        cfg.set("timeout", 30)
        assert cfg.get("timeout") == 30.0

        cfg.set("keep_ansi", "on")
        assert cfg.get("keep_ansi") is True

        cfg.set("encoding", "gbk")
        assert cfg.get("encoding") == "gbk"

    def test_off_value(self, cfg):
        """测试 'off' 字符串转为 False"""
        cfg.set("keep_ansi", "off")
        assert cfg.get("keep_ansi") is False

    def test_get_all(self, cfg):
        """测试 get_all 返回完整配置"""
        all_cfg = cfg.get_all()
        assert isinstance(all_cfg, dict)
        assert "timeout" in all_cfg
        assert "encoding" in all_cfg

    def test_show_single(self, cfg):
        """测试 show 展示单个配置项"""
        cfg.set("timeout", 45)
        text = cfg.show("timeout")
        assert "45" in text

    def test_show_all(self, cfg):
        """测试 show 展示全部配置项"""
        text = cfg.show()
        assert "当前调用配置" in text
        assert "timeout" in text
        assert "encoding" in text

    def test_show_unknown_key(self, cfg):
        """测试展示未知配置项"""
        text = cfg.show("nonexistent")
        assert "未知配置项" in text

    def test_set_invalid_key(self, cfg):
        """测试设置无效配置键"""
        with pytest.raises(ValueError):
            cfg.set("invalid_key", "value")

    def test_on_off_conversion(self, cfg):
        """测试 on/off 字符串自动转换为 bool"""
        cfg.set("newline", "on")
        assert cfg.get("newline") is True

        cfg.set("keep_ansi", "off")
        assert cfg.get("keep_ansi") is False

    def test_timeout_float_conversion(self, cfg):
        """测试 timeout 字符串自动转为 float"""
        cfg.set("timeout", "60")
        assert cfg.get("timeout") == 60.0
        assert isinstance(cfg.get("timeout"), float)

    def test_debug_default_off(self, cfg):
        """测试 debug 默认关闭"""
        assert cfg.get("debug") is False

    def test_debug_set_off(self, cfg):
        """测试 debug 设置为 off"""
        cfg.set("debug", "off")
        assert cfg.get("debug") is False

    def test_debug_set_on(self, cfg):
        """测试 debug 设置为 on"""
        cfg.set("debug", False)
        assert cfg.get("debug") is False
        cfg.set("debug", "on")
        assert cfg.get("debug") is True

    def test_debug_set_bool(self, cfg):
        """测试 debug 直接设置 bool"""
        cfg.set("debug", False)
        assert cfg.get("debug") is False
        cfg.set("debug", True)
        assert cfg.get("debug") is True

    def test_debug_show(self, cfg):
        """测试 show 展示 debug 配置"""
        text = cfg.show("debug")
        assert "debug" in text
        assert "off" in text
        cfg.set("debug", "on")
        text = cfg.show("debug")
        assert "on" in text


# ---- ConfigParser 集成测试 ----

class TestConfigParserIntegration:
    """配置解析集成测试（测试 cli.common_args 的 _parse_default_key）"""

    def test_key_conversion(self):
        """测试 CLI 键名到内部键名的转换"""
        from src.cli.common_args import _parse_default_key, _format_config_key

        assert _parse_default_key("output-by-natural-language") == "output_by_natural_language"
        assert _format_config_key("output_by_natural_language") == "output-by-natural-language"
        assert _parse_default_key("keep-ansi") == "keep_ansi"
        assert _format_config_key("keep_ansi") == "keep-ansi"
        assert _parse_default_key("timeout") == "timeout"


class TestUnescapeJsonString:
    """JSON 风格转义解码测试"""

    def test_unescape_double_quote(self):
        """测试 \\" → 字面引号"""
        from src.input.text import unescape_json_string

        assert unescape_json_string("\\\"hello\\\"") == '"hello"'

    def test_unescape_backslash(self):
        """测试 \\\\ → 字面反斜杠"""
        from src.input.text import unescape_json_string

        assert unescape_json_string("path\\\\to\\\\file") == "path\\to\\file"

    def test_unescape_path_backslash_r_preserved(self):
        """测试 Windows 路径中的 \\r 不被当作回车转义"""
        from src.input.text import unescape_json_string

        assert unescape_json_string("C:\\Users\\rikka\\Desktop") == "C:\\Users\\rikka\\Desktop"

    def test_unescape_path_backslash_t_preserved(self):
        """测试 Windows 路径中的 \\t 不被当作制表符转义"""
        from src.input.text import unescape_json_string

        assert unescape_json_string("third_party") == "third_party"

    def test_unescape_complex_command(self):
        """测试复杂命令中的引号转义

        模拟用户实际场景：g++ 编译命令含多个带空格路径
        """
        from src.input.text import unescape_json_string

        cmd = (
            "& \\\"C:\\\\Program Files\\\\g++.exe\\\""
            " -I\\\"C:\\\\路径 含空格\\\\include\\\""
            " \\\"C:\\\\src\\\\main.cpp\\\""
            " -o \\\"C:\\\\build\\\\app.exe\\\""
        )
        expected = (
            '& "C:\\Program Files\\g++.exe"'
            ' -I"C:\\路径 含空格\\include"'
            ' "C:\\src\\main.cpp"'
            ' -o "C:\\build\\app.exe"'
        )
        assert unescape_json_string(cmd) == expected

    def test_unescape_no_effect_on_plain_text(self):
        """测试纯文本不受影响"""
        from src.input.text import unescape_json_string

        assert unescape_json_string("hello world") == "hello world"
        assert unescape_json_string("g++ -std=c++17 file.cpp") == "g++ -std=c++17 file.cpp"

    def test_unescape_unknown_escape_preserved(self):
        """测试不识别的转义序列保留原样"""
        from src.input.text import unescape_json_string

        assert unescape_json_string("\\x\\z") == "\\x\\z"

    def test_process_input_newline(self):
        """测试 process_input 解码 \\n （需启用 json_escaping）"""
        from src.input.text import process_input

        result, _ = process_input("line1\\nline2", json_escaping=True)
        assert result == "line1\nline2\r"

    def test_process_input_backslash_in_path(self):
        """测试路径中双反斜杠经 JSON 解码后变为单反斜杠（需启用 json_escaping）"""
        from src.input.text import process_input

        result, _ = process_input("cd C:\\\\Users", json_escaping=True)
        # json.loads 将 \\\\ 解码为 \\，路径变为 cd C:\Users
        assert result == "cd C:\\Users\r"
