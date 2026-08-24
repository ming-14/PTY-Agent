"""插件配置单测 — 分层合并、schema 校验、config.yaml 自愈、set 持久化"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.config import ConfigError, PluginConfig  # noqa: E402
from tests.helpers import write_config_yaml  # noqa: E402


def _make(plugin_dir, defaults=None, schema=None, plugin_id="demo"):
    return PluginConfig(plugin_id, plugin_dir, defaults or {}, schema)


class TestLayering:
    def test_defaults_only(self, tmp_path):
        cfg = _make(str(tmp_path), {"a": 1, "b": "x"})
        assert cfg.get("a") == 1
        assert cfg.get("b") == "x"
        assert cfg.get("missing", 42) == 42

    def test_yaml_overrides_defaults(self, tmp_path):
        write_config_yaml(str(tmp_path), {"a": 2})
        cfg = _make(str(tmp_path), {"a": 1, "b": "x"})
        assert cfg.get("a") == 2
        assert cfg.get("b") == "x"

    def test_missing_yaml_generated(self, tmp_path):
        cfg = _make(str(tmp_path), {"a": 1})
        assert os.path.isfile(os.path.join(str(tmp_path), "config.yaml"))
        # 生成文件内容即默认值
        cfg2 = _make(str(tmp_path), {"a": 1})
        assert cfg2.get("a") == 1

    def test_env_overrides(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PTY_PLUGIN_DEMO_A", "9")
        cfg = _make(str(tmp_path), {"a": 1})
        assert cfg.get("a") == 9

    def test_env_coerce_bool_and_int(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PTY_PLUGIN_DEMO_FLAG", "true")
        monkeypatch.setenv("PTY_PLUGIN_DEMO_SIZE", "2048")
        cfg = _make(str(tmp_path), {"flag": False, "size": 100})
        assert cfg.get("flag") is True
        assert cfg.get("size") == 2048

    def test_env_prefix_normalized(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PTY_PLUGIN_MY_PLUGIN_X", "1")
        cfg = PluginConfig("my-plugin", str(tmp_path), {"x": 0}, None)
        assert cfg.get("x") == 1


class TestSchemaValidation:
    SCHEMA = {
        "type": "object",
        "properties": {
            "size": {"type": "integer", "minimum": 1},
            "name": {"type": "string", "maxLength": 10},
            "tags": {"type": "array", "items": {"type": "string"}},
            "rg": {"type": ["string", "null"]},
            "mode": {"type": "string", "enum": ["fast", "safe"]},
            "extra": {"type": "object", "additionalProperties": False},
        },
        "required": ["size"],
    }

    def test_valid_passes(self, tmp_path):
        cfg = _make(str(tmp_path), {"size": 5, "name": "ok", "tags": ["a"]}, self.SCHEMA)
        assert cfg.get("size") == 5

    def test_invalid_type_fails(self, tmp_path):
        with pytest.raises(ConfigError):
            _make(str(tmp_path), {"size": "big"}, self.SCHEMA)

    def test_below_minimum_fails(self, tmp_path):
        with pytest.raises(ConfigError):
            _make(str(tmp_path), {"size": 0}, self.SCHEMA)

    def test_enum_fails(self, tmp_path):
        with pytest.raises(ConfigError):
            _make(str(tmp_path), {"size": 1, "mode": "turbo"}, self.SCHEMA)

    def test_nullable_type(self, tmp_path):
        cfg = _make(str(tmp_path), {"size": 1, "rg": None}, self.SCHEMA)
        assert cfg.get("rg") is None

    def test_additional_properties_false(self, tmp_path):
        with pytest.raises(ConfigError):
            _make(str(tmp_path), {"size": 1, "extra": {"unknown": 1}}, self.SCHEMA)

    def test_missing_required_fails(self, tmp_path):
        with pytest.raises(ConfigError):
            _make(str(tmp_path), {}, self.SCHEMA)

    def test_bad_yaml_fails(self, tmp_path):
        with open(os.path.join(str(tmp_path), "config.yaml"), "w", encoding="utf-8") as f:
            f.write("a: [unclosed\n")
        with pytest.raises(ConfigError):
            _make(str(tmp_path), {"size": 1}, self.SCHEMA)


class TestSet:
    def test_set_persists_and_validates(self, tmp_path):
        schema = {"type": "object", "properties": {"size": {"type": "integer"}}}
        cfg = _make(str(tmp_path), {"size": 1}, schema)
        cfg.set("size", 5)
        # 重新加载：值已持久化
        cfg2 = _make(str(tmp_path), {"size": 1}, schema)
        assert cfg2.get("size") == 5

    def test_set_invalid_rejected(self, tmp_path):
        schema = {"type": "object", "properties": {"size": {"type": "integer"}}}
        cfg = _make(str(tmp_path), {"size": 1}, schema)
        with pytest.raises(ConfigError):
            cfg.set("size", "nope")
        assert cfg.get("size") == 1
