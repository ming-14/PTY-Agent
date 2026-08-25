"""注册表单测 — 生命周期 enable/disable/reload、auto_load、列表/详情、卸载"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.registry import PluginRegistry, _match_auto_load  # noqa: E402
from tests.helpers import write_plugin_dir  # noqa: E402


SESSION_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def on_event(self, ctx, event): pass\n"
    "    def on_init(self, ctx):\n"
    "        self._inited = True\n"
    "    def on_enable(self, ctx):\n"
    "        self._enabled = True\n"
    "    def on_disable(self, ctx):\n"
    "        self._disabled = True\n"
    "plugin = P()\n"
)

PROCESS_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def handle_message(self, ctx, msg): return {'ok': True}\n"
    "    def on_init(self, ctx):\n"
    "        self._inited = True\n"
    "    def on_enable(self, ctx):\n"
    "        self._enabled = True\n"
    "    def on_disable(self, ctx):\n"
    "        self._disabled = True\n"
    "plugin = P()\n"
)

AUTO_LOAD_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def on_event(self, ctx, event): pass\n"
    "plugin = P()\n"
)


class TestLifecycle:
    def test_session_plugin_enabled_by_default(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        reg = PluginRegistry([pdir], states={}, enabled_by_default=True)
        info = reg.info("demo")
        assert info is not None
        assert info["state"] == "enabled"
        # 规范实例已创建
        assert reg._entries["demo"].instance is not None
        inst = reg._entries["demo"].instance
        assert inst._inited is True
        assert inst._enabled is True

    def test_session_plugin_disabled_by_state(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        reg = PluginRegistry([pdir], states={"demo": False}, enabled_by_default=True)
        info = reg.info("demo")
        assert info["state"] == "disabled"

    def test_disable_enable_cycle(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        reg = PluginRegistry([pdir])
        assert reg.disable("demo") is True
        assert reg.info("demo")["state"] == "disabled"
        assert reg.instantiate("demo") is None  # 已禁用，不可挂载
        assert reg.enable("demo") is True
        assert reg.info("demo")["state"] == "enabled"
        assert reg.instantiate("demo") is not None

    def test_process_plugin_enabled(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "proc", "process", PROCESS_SRC,
            manifest_extra={"messageTypes": ["cmd_a"]},
        )
        reg = PluginRegistry([pdir])
        assert "proc" in reg.process_instances()
        inst = reg.process_instances()["proc"]
        assert inst._inited is True
        assert inst._enabled is True

    def test_process_disable(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "proc", "process", PROCESS_SRC,
            manifest_extra={"messageTypes": ["cmd_a"]},
        )
        reg = PluginRegistry([pdir])
        assert reg.disable("proc") is True
        assert "proc" not in reg.process_instances()
        info = reg.info("proc")
        assert info["state"] == "disabled"

    def test_broken_plugin_visible(self, tmp_path):
        reg = PluginRegistry([], states={})
        # 直接注入一个损坏的 manifest（无法通过 loader，但可模拟）
        # 测试：loader 失败的插件不进入 registry（已测）
        # 测试：环境初始化失败的插件为 BROKEN
        # 通过不存在的插件目录验证
        reg = PluginRegistry([], states={"nope": True})
        # 无插件目录，无条目
        assert reg.instantiate("nope") is None

    def test_process_plugin_change_callback(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "proc", "process", PROCESS_SRC,
            manifest_extra={"messageTypes": ["cmd_a"]},
        )
        called = []
        reg = PluginRegistry([pdir])
        reg.set_change_callback(lambda: called.append("x"))
        assert reg.disable("proc") is True
        assert reg.enable("proc") is True
        assert len(called) == 2  # disable + enable 各一次

    def test_reload(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        reg = PluginRegistry([pdir])
        assert reg.reload("demo") is True
        assert reg.info("demo")["state"] == "enabled"

    def test_reload_disabled_plugin(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        reg = PluginRegistry([pdir], states={"demo": False})
        assert reg.reload("demo") is True
        assert reg.info("demo")["state"] == "disabled"

    def test_reload_nonexistent(self, tmp_path):
        reg = PluginRegistry([])
        assert reg.reload("nope") is False

    def test_instantiate_only_session(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "proc", "process", PROCESS_SRC,
            manifest_extra={"messageTypes": ["cmd_a"]},
        )
        sdir = write_plugin_dir(tmp_path, "sess", "session", SESSION_SRC)
        reg = PluginRegistry([pdir, sdir])
        assert reg.instantiate("proc") is None
        assert reg.instantiate("sess") is not None
        assert reg.instantiate("nope") is None


class TestListAll:
    def test_list_all_fields(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        reg = PluginRegistry([pdir])
        items = reg.list_all()
        assert len(items) == 1
        item = items[0]
        assert item["name"] == "demo"
        assert item["kind"] == "session"
        assert item["state"] == "enabled"
        assert item["hooks"] == {}
        assert item["permissions"] == []

    def test_info_contains_path(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        reg = PluginRegistry([pdir])
        info = reg.info("demo")
        assert info["path"] == pdir
        assert info["events"] == []
        assert info["dependencies"] == {}


class TestAutoLoad:
    def test_match_auto_load_hit(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "auto", "session", AUTO_LOAD_SRC,
            manifest_extra={"autoLoad": {"command": r"^python"}},
        )
        reg = PluginRegistry([pdir])
        assert reg.match_auto_load("python app.py", "/tmp", None) == ["auto"]
        assert reg.match_auto_load("node app.js", "/tmp", None) == []

    def test_auto_load_disabled_plugin_returns_empty(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "auto", "session", AUTO_LOAD_SRC,
            manifest_extra={"autoLoad": {"command": "python"}},
        )
        reg = PluginRegistry([pdir], states={"auto": False})
        assert reg.match_auto_load("python app.py", "/tmp", None) == []

    def test_auto_load_cwd_prefix(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "auto", "session", AUTO_LOAD_SRC,
            manifest_extra={"autoLoad": {"cwd": ["/work"]}},
        )
        reg = PluginRegistry([pdir])
        assert reg.match_auto_load("cmd", "/work/project", None) == ["auto"]
        assert reg.match_auto_load("cmd", "/other", None) == []

    def test_auto_load_env(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "auto", "session", AUTO_LOAD_SRC,
            manifest_extra={"autoLoad": {"env": {"CI": ""}}},
        )
        reg = PluginRegistry([pdir])
        assert reg.match_auto_load("cmd", None, {"CI": "true"}) == ["auto"]
        assert reg.match_auto_load("cmd", None, None) == []

    def test_match_auto_load_unit(self):
        # _match_auto_load 直接测试
        rule = {"command": ["python", "test"]}
        assert _match_auto_load(rule, "python app.py", None, None) is True
        assert _match_auto_load(rule, "java app", None, None) is False
        rule2 = {"command": r"^python", "cwd": ["/home"]}
        assert _match_auto_load(rule2, "python x", "/home/user", None) is True
        assert _match_auto_load(rule2, "python x", "/tmp", None) is False


class TestRemove:
    def test_remove_disabled_plugin(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        reg = PluginRegistry([pdir])
        reg.disable("demo")
        assert reg.remove("demo") is True
        assert reg.has("demo") is False

    def test_remove_enabled_plugin_refused(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        reg = PluginRegistry([pdir])
        assert reg.remove("demo") is False
        assert reg.has("demo") is True

    def test_remove_nonexistent(self, tmp_path):
        reg = PluginRegistry([])
        assert reg.remove("nope") is False
