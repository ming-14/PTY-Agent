"""插件配置单测 — 内存态：默认值、set 内存覆盖、schema 校验"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.config import ConfigError, PluginConfig  # noqa: E402


def _make(defaults=None, schema=None):
    return PluginConfig(defaults or {}, schema)


class TestDefaults:
    def test_defaults_only(self):
        cfg = _make({"a": 1, "b": "x"})
        assert cfg.get("a") == 1
        assert cfg.get("b") == "x"
        assert cfg.get("missing", 42) == 42

    def test_as_dict(self):
        cfg = _make({"a": 1, "b": 2})
        assert cfg.as_dict() == {"a": 1, "b": 2}


class TestSet:
    def test_set_updates_memory(self):
        cfg = _make({"size": 1})
        cfg.set("size", 5)
        assert cfg.get("size") == 5
        # 重启后应恢复默认（模拟新实例）
        cfg2 = _make({"size": 1})
        assert cfg2.get("size") == 1

    def test_set_adds_new_key(self):
        cfg = _make({"a": 1})
        cfg.set("b", 2)
        assert cfg.get("b") == 2

    def test_set_invalid_rejected(self):
        schema = {"type": "object", "properties": {"size": {"type": "integer"}}}
        cfg = _make({"size": 1}, schema)
        with pytest.raises(ConfigError):
            cfg.set("size", "nope")
        assert cfg.get("size") == 1

    def test_set_validates_schema(self):
        schema = {"type": "object", "properties": {"mode": {"type": "string", "enum": ["fast", "safe"]}}}
        cfg = _make({"mode": "fast"}, schema)
        with pytest.raises(ConfigError):
            cfg.set("mode", "turbo")
        assert cfg.get("mode") == "fast"


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

    def test_valid_passes(self):
        cfg = _make({"size": 5, "name": "ok", "tags": ["a"]}, self.SCHEMA)
        assert cfg.get("size") == 5

    def test_invalid_type_fails(self):
        with pytest.raises(ConfigError):
            _make({"size": "big"}, self.SCHEMA)

    def test_below_minimum_fails(self):
        with pytest.raises(ConfigError):
            _make({"size": 0}, self.SCHEMA)

    def test_enum_fails(self):
        with pytest.raises(ConfigError):
            _make({"size": 1, "mode": "turbo"}, self.SCHEMA)

    def test_nullable_type(self):
        cfg = _make({"size": 1, "rg": None}, self.SCHEMA)
        assert cfg.get("rg") is None

    def test_additional_properties_false(self):
        with pytest.raises(ConfigError):
            _make({"size": 1, "extra": {"unknown": 1}}, self.SCHEMA)

    def test_missing_required_fails(self):
        with pytest.raises(ConfigError):
            _make({}, self.SCHEMA)


class TestReset:
    def test_reset_restores_defaults(self):
        cfg = _make({"a": 1, "b": 2})
        cfg.set("a", 99)
        cfg.set("c", 3)
        cfg.reset()
        assert cfg.get("a") == 1
        assert cfg.get("b") == 2
        assert cfg.get("c") is None