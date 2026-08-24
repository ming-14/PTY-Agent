"""manifest 单测 — plugin.json 解析与结构校验"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.manifest import (  # noqa: E402
    MANIFEST_FILE,
    PluginManifest,
    load_manifest,
)


def _write_manifest(tmp_path, data: dict) -> str:
    pdir = tmp_path / "plug"
    pdir.mkdir()
    (pdir / MANIFEST_FILE).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    return str(pdir)


def _load(tmp_path, data: dict):
    return load_manifest(_write_manifest(tmp_path, data))


class TestValidManifest:
    def test_minimal_session(self, tmp_path):
        m = _load(tmp_path, {"id": "demo", "version": "1.2.3", "kind": "session"})
        assert m is not None
        assert m.id == "demo"
        assert m.version == "1.2.3"
        assert m.kind == "session"
        assert m.triggers == []
        assert m.entry == "__init__.py"
        assert m.needs_io is False

    def test_full_manifest(self, tmp_path):
        m = _load(tmp_path, {
            "id": "files",
            "version": "2.0.0",
            "kind": "process",
            "description": "文件工具",
            "messageTypes": ["file_read", "file_write"],
            "needsIO": True,
            "hooks": {"handle_message": {"priority": 120}},
            "permissions": {"required": ["filesystem.read", "filesystem.write"]},
            "config": {"defaults": {"max_size": 1024}},
            "events": {"subscribe": ["session.*"]},
            "dependencies": {"plugins": {"state_check": ">=1.0.0"}},
        })
        assert m is not None
        assert m.message_types == ["file_read", "file_write"]
        assert m.needs_io is True
        assert m.hooks == {"handle_message": {"priority": 120}}
        assert m.permissions == ["filesystem.read", "filesystem.write"]
        assert m.config_defaults == {"max_size": 1024}
        assert m.events == ["session.*"]

    def test_session_with_triggers(self, tmp_path):
        m = _load(tmp_path, {
            "id": "watch",
            "version": "1.0",
            "kind": "session",
            "triggers": ["event", "poll"],
            "pollInterval": 2.5,
            "autoLoad": {"command": r"^python", "cwd": ["/work"], "env": {"CI": ""}},
        })
        assert m is not None
        assert m.triggers == ["event", "poll"]
        assert m.poll_interval == 2.5
        assert m.auto_load == {"command": r"^python", "cwd": ["/work"], "env": {"CI": ""}}

    def test_cli_manifest(self, tmp_path):
        m = _load(tmp_path, {
            "id": "simple",
            "version": "1.0",
            "kind": "cli",
            "commands": ["exec", "send"],
            "hooks": {"render_response": {}},
        })
        assert m is not None
        assert m.kind == "cli"
        assert m.commands == ["exec", "send"]

    def test_config_schema_loaded(self, tmp_path):
        pdir = tmp_path / "plug"
        pdir.mkdir()
        (pdir / MANIFEST_FILE).write_text(
            json.dumps({"id": "x", "version": "1.0", "kind": "session"}), encoding="utf-8"
        )
        (pdir / "config.schema.json").write_text(
            json.dumps({"type": "object", "properties": {"a": {"type": "integer"}}}),
            encoding="utf-8",
        )
        m = load_manifest(str(pdir))
        assert m is not None
        assert m.config_schema == {"type": "object", "properties": {"a": {"type": "integer"}}}

    def test_config_schema_bad_json_ignored(self, tmp_path):
        pdir = tmp_path / "plug"
        pdir.mkdir()
        (pdir / MANIFEST_FILE).write_text(
            json.dumps({"id": "x", "version": "1.0", "kind": "session"}), encoding="utf-8"
        )
        (pdir / "config.schema.json").write_text("{not json", encoding="utf-8")
        m = load_manifest(str(pdir))
        assert m is not None
        assert m.config_schema is None


class TestInvalidManifest:
    @pytest.mark.parametrize("data", [
        {"version": "1.0", "kind": "session"},            # 缺 id
        {"id": "Bad ID!", "version": "1.0", "kind": "session"},  # id 非法字符
        {"id": "demo", "kind": "session"},                # 缺 version
        {"id": "demo", "version": "1.0", "kind": "nope"},  # kind 非法
        {"id": "demo", "version": "1.0", "kind": "process",
         "triggers": ["event"]},                          # triggers 仅 session
        {"id": "demo", "version": "1.0", "kind": "session",
         "triggers": ["poll"]},                           # poll 缺 pollInterval
        {"id": "demo", "version": "1.0", "kind": "session",
         "triggers": ["poll"], "pollInterval": -1},
        {"id": "demo", "version": "1.0", "kind": "session",
         "autoLoad": {"cmd": "python"}},                  # autoLoad 未知键
        {"id": "demo", "version": "1.0", "kind": "process",
         "autoLoad": {"command": "python"}},              # autoLoad 仅 session
        {"id": "demo", "version": "1.0", "kind": "process",
         "messageTypes": ["x"], "needsIO": "yes"},        # needsIO 非布尔
        {"id": "demo", "version": "1.0", "kind": "session",
         "messageTypes": ["file_read"]},                  # messageTypes 仅 process
        {"id": "demo", "version": "1.0", "kind": "process",
         "hooks": {"on_input": {"priority": "high"}}},    # priority 非整数
        {"id": "demo", "version": "1.0", "kind": "session",
         "permissions": {"required": 42}},
        {"id": "demo", "version": "1.0", "kind": "session",
         "config": {"defaults": "nope"}},
        {"id": "demo", "version": "1.0", "kind": "session",
         "events": {"subscribe": "session.*"}},
    ])
    def test_invalid_returns_none(self, tmp_path, data):
        assert _load(tmp_path, data) is None

    def test_missing_file(self, tmp_path):
        assert load_manifest(str(tmp_path / "nope")) is None

    def test_bad_json(self, tmp_path):
        pdir = tmp_path / "plug"
        pdir.mkdir()
        (pdir / MANIFEST_FILE).write_text("{bad", encoding="utf-8")
        assert load_manifest(str(pdir)) is None

    def test_top_level_not_object(self, tmp_path):
        pdir = tmp_path / "plug"
        pdir.mkdir()
        (pdir / MANIFEST_FILE).write_text("[1,2]", encoding="utf-8")
        assert load_manifest(str(pdir)) is None
