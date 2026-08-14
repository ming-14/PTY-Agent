"""测试配置项 ai_analyse / ai_prompt 的验证与存取"""

import importlib.util
import os

import pytest

_CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "src", "client", "config_manager.py")
spec = importlib.util.spec_from_file_location("cfg_test", _CFG_PATH)
cfg_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cfg_mod)

ConfigManager = cfg_mod.ConfigManager


class TestAiAnalyseConfig:
    """ai_analyse 配置项验证"""

    def test_default_value(self):
        cfg = ConfigManager()
        assert cfg.get("ai_analyse") == "none"

    def test_valid_values(self):
        cfg = ConfigManager()
        for val in ("none", "fileOutput", "responseOutput"):
            cfg.set("ai_analyse", val)
            assert cfg.get("ai_analyse") == val

    def test_invalid_value_raises(self):
        cfg = ConfigManager()
        with pytest.raises(ValueError, match="Invalid ai-analyse value"):
            cfg.set("ai_analyse", "invalid")
        with pytest.raises(ValueError, match="Invalid ai-analyse value"):
            cfg.set("ai_analyse", "FileOutput")
        with pytest.raises(ValueError, match="Invalid ai-analyse value"):
            cfg.set("ai_analyse", "")

    def test_show_includes_ai_analyse(self):
        cfg = ConfigManager()
        cfg.set("ai_analyse", "fileOutput")
        display = cfg.show()
        assert "ai_analyse" in display
        assert "fileOutput" in display


class TestAiPromptConfig:
    """ai_prompt 配置项验证"""

    def test_default_value(self):
        cfg = ConfigManager()
        prompt = cfg.get("ai_prompt")
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "全面分析" in prompt

    def test_valid_values(self):
        cfg = ConfigManager()
        for val in ("分析", "Check this", "ユーザー入力"):
            cfg.set("ai_prompt", val)
            assert cfg.get("ai_prompt") == val

    def test_empty_string_raises(self):
        cfg = ConfigManager()
        with pytest.raises(ValueError, match="Invalid ai-prompt value"):
            cfg.set("ai_prompt", "")
        with pytest.raises(ValueError, match="Invalid ai-prompt value"):
            cfg.set("ai_prompt", "   ")

    def test_show_includes_prompt(self):
        cfg = ConfigManager()
        cfg.set("ai_prompt", "自定义分析")
        display = cfg.show()
        assert "ai_prompt" in display
        assert "自定义分析" in display


class TestConfigShow:
    """show 方法集成验证"""

    def test_show_all(self):
        cfg = ConfigManager()
        cfg.set("ai_analyse", "responseOutput")
        cfg.set("ai_prompt", "分析")
        display = cfg.show()
        assert "ai_analyse = responseOutput" in display
        assert "ai_prompt = 分析" in display

    def test_show_single(self):
        cfg = ConfigManager()
        cfg.set("ai_analyse", "fileOutput")
        single = cfg.show("ai_analyse")
        assert "fileOutput" in single
        unknown = cfg.show("nonexistent")
        assert "未知" in unknown