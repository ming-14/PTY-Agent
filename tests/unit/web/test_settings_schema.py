"""web/domain/settings_schema.py 单元测试

验证设置项 Schema 的元数据正确性：
- VALID_KEYS 内容
- _KEY_TO_CONFIG_NAME 映射完整性
- get_defaults() 从 config.daemon 正确读取默认值
"""

import logging

import pytest

from src.web.domain import settings_schema
from src.config import daemon as daemon_config


# ── 期望的 key 集合（与后端 _KEY_TO_CONFIG_NAME 保持一致） ──
# remote.vncEnabled / remote.fsEnabled 属部署级配置（web.toml 的 ENABLE_VNC /
# ENABLE_FASTSCREEN），前端不可修改，故不在 VALID_KEYS 中
_EXPECTED_VALID_KEYS = {
    "basic.theme",
    "rikka.enabled",
    "ime.enabled",
    "ime.candidateCount",
    "ime.vertical",
    "ime.defaultState",
    "ime.keyboardLayout",
    "ime.toolbarDisplay",
    "ime.tbOpacity",
    "ime.kbOpacity",
    "ime.tbScale",
    "ime.kbScale",
    "remote.fsFps",
    "remote.fsBitrate",
    "remote.fsStreamFormat",
}


class TestValidKeys:
    """VALID_KEYS 集合内容验证"""

    def test_valid_keys_contains_all_expected(self):
        """VALID_KEYS 应包含所有期望的设置项 key"""
        assert settings_schema.VALID_KEYS == _EXPECTED_VALID_KEYS

    def test_valid_keys_not_empty(self):
        assert len(settings_schema.VALID_KEYS) > 0

    def test_deleted_keys_not_present(self):
        """已删除/不应出现的 key 不在 VALID_KEYS 中"""
        # remote.vncEnabled / remote.fsEnabled 已改为部署级配置（web.toml 管理）
        # basic.fontSize 等是历史已删除的旧 key
        deleted_keys = [
            "basic.fontSize", "basic.defaultSize", "basic.eol",
            "basic.mouseMode", "basic.sidebarCollapsed", "basic.sidebarWidth",
            "ime.scheme", "ime.hotkey",
            "remote.vncPath", "remote.sessionTimeout",
            "remote.vncEnabled", "remote.fsEnabled",
        ]
        for k in deleted_keys:
            assert k not in settings_schema.VALID_KEYS, f"key '{k}' 不应出现在 VALID_KEYS"


class TestKeyToConfigMapping:
    """_KEY_TO_CONFIG_NAME 映射验证"""

    def test_mapping_covers_all_valid_keys(self):
        """每个 VALID_KEY 都有对应的 config 常量名"""
        for key in settings_schema.VALID_KEYS:
            assert key in settings_schema._KEY_TO_CONFIG_NAME, f"key '{key}' 缺少 config 映射"

    def test_mapping_values_are_uppercase(self):
        """config 常量名应为大写（遵循 config.daemon flatten 后的命名约定）"""
        for key, config_name in settings_schema._KEY_TO_CONFIG_NAME.items():
            assert config_name == config_name.upper(), \
                f"key '{key}' 的 config 名 '{config_name}' 应为大写"

    def test_no_restart_required_keys_mapping(self):
        """remote.vncEnabled / remote.fsEnabled 不应在映射中（属部署级配置）"""
        assert "remote.vncEnabled" not in settings_schema._KEY_TO_CONFIG_NAME
        assert "remote.fsEnabled" not in settings_schema._KEY_TO_CONFIG_NAME


class TestGetDefaults:
    """get_defaults() 函数验证"""

    def test_returns_dict(self):
        """get_defaults() 应返回 dict"""
        result = settings_schema.get_defaults()
        assert isinstance(result, dict)

    def test_defaults_contain_all_keys_present_in_config(self):
        """config.daemon 中存在的常量应出现在 defaults 中"""
        result = settings_schema.get_defaults()
        for key, config_name in settings_schema._KEY_TO_CONFIG_NAME.items():
            if hasattr(daemon_config, config_name):
                assert key in result, f"key '{key}' (config={config_name}) 存在于 config 但未出现在 defaults"

    def test_defaults_values_match_config(self):
        """defaults 值应与 config.daemon 中的常量值一致"""
        result = settings_schema.get_defaults()
        for key, config_name in settings_schema._KEY_TO_CONFIG_NAME.items():
            if hasattr(daemon_config, config_name):
                expected = getattr(daemon_config, config_name)
                assert result[key] == expected, \
                    f"key '{key}' 默认值 {result[key]} != config.{config_name}={expected}"

    def test_defaults_theme_is_string(self):
        """basic.theme 默认值应为字符串"""
        result = settings_schema.get_defaults()
        if "basic.theme" in result:
            assert isinstance(result["basic.theme"], str)

    def test_defaults_ime_enabled_is_bool(self):
        """ime.enabled 默认值应为布尔"""
        result = settings_schema.get_defaults()
        if "ime.enabled" in result:
            assert isinstance(result["ime.enabled"], bool)

    def test_defaults_fs_fps_is_int(self):
        """remote.fsFps 默认值应为整数"""
        result = settings_schema.get_defaults()
        if "remote.fsFps" in result:
            assert isinstance(result["remote.fsFps"], int)

    def test_get_defaults_with_missing_config_attr(self, monkeypatch, caplog):
        """config.daemon 中缺失的常量应跳过并记录警告日志

        注意：config.daemon 模块通过 globals().update(_all) 动态注入属性，
        monkeypatch.delattr 后由 pytest teardown 自动恢复（raise=False 时不会
        在 setup 阶段报错，也不会在 teardown 阶段尝试 setattr 一个被删的属性）。
        故这里**不要**手动 setattr 恢复，否则会触发 AttributeError。
        """
        # 前置断言：DEFAULT_THEME 应确实存在（web.toml 提供）
        assert hasattr(daemon_config, "DEFAULT_THEME"), \
            "前置条件失败：daemon_config 应有 DEFAULT_THEME 属性"

        # 临时删除该属性，模拟 config 中缺失某个常量
        monkeypatch.delattr(daemon_config, "DEFAULT_THEME", raising=False)

        with caplog.at_level(logging.WARNING, logger="pty-web-settings"):
            result = settings_schema.get_defaults()

        # 缺失的常量不应出现在 defaults 中
        assert "basic.theme" not in result

        # 应记录 WARNING 日志，提示该 config 常量未找到
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("DEFAULT_THEME" in r.getMessage() for r in warning_records), \
            "应记录包含 'DEFAULT_THEME' 的 WARNING 日志"
