"""ConfigManager 与 Formatter 单元测试

测试客户端配置管理、自然语言输出。"""
import pytest


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
        assert cfg.get("debug") is True

    def test_set_and_get(self, cfg):
        """测试设置值并读取"""
        cfg.set("timeout", 30)
        assert cfg.get("timeout") == 30.0

    def test_off_value(self, cfg):
        """测试 'off' 字符串转为 False"""
        cfg.set("newline", "off")
        assert cfg.get("newline") is False

    def test_get_all(self, cfg):
        """测试 get_all 返回完整配置"""
        all_cfg = cfg.get_all()
        assert isinstance(all_cfg, dict)
        assert "timeout" in all_cfg

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

        cfg.set("debug", "off")
        assert cfg.get("debug") is False

    def test_timeout_float_conversion(self, cfg):
        """测试 timeout 字符串自动转为 float"""
        cfg.set("timeout", "60")
        assert cfg.get("timeout") == 60.0
        assert isinstance(cfg.get("timeout"), float)

    def test_debug_default_on(self, cfg):
        """测试 debug 默认开启"""
        assert cfg.get("debug") is True

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
        assert "on" in text
        cfg.set("debug", "off")
        text = cfg.show("debug")
        assert "off" in text

    def test_send_eol_default_lf(self, cfg):
        """send_eol 默认值为 lf"""
        assert cfg.get("send_eol") == "lf"

    def test_send_eol_set_valid(self, cfg):
        """设置合法 send_eol 值"""
        cfg.set("send_eol", "crlf")
        assert cfg.get("send_eol") == "crlf"
        cfg.set("send_eol", "CR")  # 大小写不敏感
        assert cfg.get("send_eol") == "cr"

    def test_send_eol_invalid(self, cfg):
        """设置非法 send_eol 值应抛 ValueError"""
        with pytest.raises(ValueError):
            cfg.set("send_eol", "br")

    def test_resolve_eol(self, cfg):
        """resolve_eol 将配置名解析为实际行尾字符串"""
        from src.client.config_manager import resolve_eol
        assert resolve_eol("lf") == "\n"
        assert resolve_eol("cr") == "\r"
        assert resolve_eol("crlf") == "\r\n"
        assert resolve_eol("unknown") == "\n"  # 未知回退 \n

    def test_process_input_eol(self):
        """process_input 使用指定行尾"""
        from src.client.input import process_input
        from src.client.config_manager import resolve_eol
        assert process_input("x", eol=resolve_eol("lf")) == "x\n"
        assert process_input("x", eol=resolve_eol("cr")) == "x\r"
        assert process_input("x", eol=resolve_eol("crlf")) == "x\r\n"


# ---- Formatter 测试 ----


class TestFormatter:
    """响应格式化输出单元测试"""

    @pytest.fixture(autouse=True)
    def reset_mode(self):
        """每个测试后重置输出模式"""
        from src.client.formatter import set_debug_mode

        yield
        set_debug_mode(True)

    def test_result_output(self, capsys):
        """测试 result 输出"""
        from src.client.formatter import print_response

        resp = {
            "type": "result",
            "session_id": "test-sess",
            "output": "Hello World",
            "output_offset": 100,
            "trigger": {"matched": True, "reason": "matched"},
            "program": {"running": True, "exit_code": None, "error_message": None},
            "debug": {"processes": [1234], "gui_windows": [], "pending_events": []},
        }
        print_response(resp)
        captured = capsys.readouterr()

        # 输出内容进入 stdout，元数据在 stderr 或 stdout
        assert "Hello World" in captured.out or "Hello World" in captured.err

    def test_error_output(self, capsys):
        """测试错误输出"""
        from src.client.formatter import print_response

        resp = {"type": "error", "error": "会话不存在"}
        print_response(resp)
        captured = capsys.readouterr()

        assert "会话不存在" in captured.err

    def test_none_response(self, capsys):
        """测试 None 响应"""
        from src.client.formatter import print_response

        print_response(None)
        captured = capsys.readouterr()

        assert "daemon not responding" in captured.err

    # ---- debug 模式测试 ----

    def test_debug_enabled(self, capsys):
        """测试 debug 开启时显示 debug 段"""
        from src.client.formatter import print_response, set_debug_mode

        set_debug_mode(True)
        resp = {
            "type": "result",
            "session_id": "test-sess",
            "output": "hello",
            "trigger_matched": True,
            "reason": "matched",
            "program": {"running": True},
            "debug": {
                "processes": [{"pid": 1234, "path": "python.exe"}],
                "gui_windows": [],
                "pending_events": [],
            },
        }
        print_response(resp)
        captured = capsys.readouterr()

        combined = captured.out + captured.err
        assert "debug" in combined
        assert "1234" in combined

    def test_debug_disabled(self, capsys):
        """测试 debug 关闭时隐藏 debug 段"""
        from src.client.formatter import print_response, set_debug_mode

        set_debug_mode(False)
        resp = {
            "type": "result",
            "session_id": "test-sess",
            "output": "hello",
            "trigger_matched": True,
            "reason": "matched",
            "program": {"running": True},
            "debug": {
                "processes": [{"pid": 1234, "path": "python.exe"}],
                "gui_windows": [],
                "pending_events": [],
            },
        }
        print_response(resp)
        captured = capsys.readouterr()

        combined = captured.out + captured.err
        assert "debug" not in combined
        assert "process tree" not in combined

    def test_debug_disabled_hides_events(self, capsys):
        """测试 debug 关闭时隐藏 pending_events"""
        from src.client.formatter import print_response, set_debug_mode

        set_debug_mode(False)
        resp = {
            "type": "result",
            "session_id": "test-sess",
            "output": "hello",
            "trigger_matched": True,
            "reason": "matched",
            "program": {"running": True},
            "debug": {
                "processes": [],
                "gui_windows": [],
                "pending_events": [
                    {
                        "time": 1000000.0,
                        "type": "process_spawn",
                        "pid": 1234,
                        "info": "PID 1234 created",
                    },
                ],
            },
        }
        print_response(resp)
        captured = capsys.readouterr()

        combined = captured.out + captured.err
        assert "events" not in combined
        assert "process_spawn" not in combined

    def test_debug_disabled_hides_gui(self, capsys):
        """测试 debug 关闭时隐藏 GUI 窗口"""
        from src.client.formatter import print_response, set_debug_mode

        set_debug_mode(False)
        resp = {
            "type": "result",
            "session_id": "test-sess",
            "output": "hello",
            "trigger_matched": True,
            "reason": "matched",
            "program": {"running": True},
            "debug": {
                "processes": [],
                "gui_windows": [
                    {"hwnd": 0x1234, "pid": 5678, "title": "test", "class_name": "cls"},
                ],
                "pending_events": [],
            },
        }
        print_response(resp)
        captured = capsys.readouterr()

        combined = captured.out + captured.err
        assert "window" not in combined
        assert "0x1234" not in combined

    def test_set_debug_mode(self):
        """测试 set_debug_mode 切换"""
        from src.client.formatter import set_debug_mode

        set_debug_mode(True)
        set_debug_mode(False)


class TestConfigParserIntegration:
    """配置解析集成测试（测试 __main__.py 的 _parse_default_key）"""

    def test_key_conversion(self):
        """测试 CLI 键名到内部键名的转换"""
        from src.__main__ import _parse_default_key, _format_config_key

        assert _parse_default_key("idle-timeout") == "idle_timeout"
        assert _format_config_key("idle_timeout") == "idle-timeout"
        assert _parse_default_key("timeout") == "timeout"


