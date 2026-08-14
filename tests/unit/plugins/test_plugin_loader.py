"""插件加载器与注册表单测"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.base import Plugin
from src.plugins.loader import (
    resolve_plugin_paths,
    load_module,
    extract_plugin_class,
    validate_plugin,
    load_plugins,
)
from src.plugins.registry import PluginRegistry, _match_auto_load


# ── 测试插件（临时目录生成） ────────────────────────────────

PLUGIN_VALID_EVENT = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    name = 'valid_event'\n"
    "    description = 'event plugin'\n"
    "    triggers = ['event']\n"
    "    def on_event(self, ctx, event): pass\n"
    "plugin = P()\n"
)

PLUGIN_VALID_POLL = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    name = 'valid_poll'\n"
    "    triggers = ['poll']\n"
    "    poll_interval = 5.0\n"
    "    def on_poll(self, ctx): pass\n"
    "plugin = P\n"
)

PLUGIN_BAD_TRIGGER = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    name = 'bad_trigger'\n"
    "    triggers = ['magic']\n"
    "plugin = P()\n"
)

PLUGIN_POLL_NO_INTERVAL = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    name = 'poll_no_interval'\n"
    "    triggers = ['poll']\n"
    "    def on_poll(self, ctx): pass\n"
    "plugin = P()\n"
)

PLUGIN_POLL_NO_IMPL = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    name = 'poll_no_impl'\n"
    "    triggers = ['poll']\n"
    "    poll_interval = 2.0\n"
    "plugin = P()\n"
)

PLUGIN_EVENT_NO_IMPL = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    name = 'event_no_impl'\n"
    "    triggers = ['event']\n"
    "plugin = P()\n"
)

PLUGIN_NO_EXPORT = "x = 1\n"

PLUGIN_BAD_EXPORT = "plugin = 42\n"

PLUGIN_IMPORT_ERROR = "raise RuntimeError('boom')\n"

PLUGIN_AUTO_LOAD = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    name = 'autoload'\n"
    "    triggers = ['event']\n"
    "    auto_load = {'command': r'python'}\n"
    "    def on_event(self, ctx, event): pass\n"
    "plugin = P()\n"
)


def _write_plugins(tmp_path, files: dict):
    """写入插件文件，返回插件目录路径"""
    pdir = tmp_path / "plugins"
    pdir.mkdir()
    for name, content in files.items():
        (pdir / name).write_text(content, encoding="utf-8")
    return str(pdir)


class TestResolvePaths:
    def test_dedupe_and_drop_missing(self, tmp_path):
        pdir = tmp_path / "plugins"
        pdir.mkdir()
        good = pdir / "a.py"
        good.write_text("x=1")
        missing = str(pdir / "nope.py")
        paths = resolve_plugin_paths([str(good), str(good), missing])
        assert paths == [str(good)]

    def test_empty_input(self):
        assert resolve_plugin_paths([]) == []

    def test_blank_entries_ignored(self, tmp_path):
        assert resolve_plugin_paths(["", "  "]) == []


class TestLoadAndValidate:
    def test_extract_instance_and_class(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"p.py": PLUGIN_VALID_EVENT})
        mod = load_module(os.path.join(pdir, "p.py"))
        cls = extract_plugin_class(mod, "p.py")
        assert cls is not None and issubclass(cls, Plugin)
        assert validate_plugin(cls)
        assert cls.name == "valid_event"

    def test_extract_class_export(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"p.py": PLUGIN_VALID_POLL})
        mod = load_module(os.path.join(pdir, "p.py"))
        cls = extract_plugin_class(mod, "p.py")
        assert cls is not None
        assert validate_plugin(cls)
        assert cls.poll_interval == 5.0

    def test_missing_export_rejected(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"p.py": PLUGIN_NO_EXPORT})
        mod = load_module(os.path.join(pdir, "p.py"))
        assert extract_plugin_class(mod, "p.py") is None

    def test_bad_export_rejected(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"p.py": PLUGIN_BAD_EXPORT})
        mod = load_module(os.path.join(pdir, "p.py"))
        assert extract_plugin_class(mod, "p.py") is None

    def test_illegal_trigger_rejected(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"p.py": PLUGIN_BAD_TRIGGER})
        cls = extract_plugin_class(load_module(os.path.join(pdir, "p.py")), "p.py")
        assert cls is not None
        assert not validate_plugin(cls)

    def test_poll_without_interval_rejected(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"p.py": PLUGIN_POLL_NO_INTERVAL})
        cls = extract_plugin_class(load_module(os.path.join(pdir, "p.py")), "p.py")
        assert cls is not None
        assert not validate_plugin(cls)

    def test_poll_without_impl_rejected(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"p.py": PLUGIN_POLL_NO_IMPL})
        cls = extract_plugin_class(load_module(os.path.join(pdir, "p.py")), "p.py")
        assert cls is not None
        assert not validate_plugin(cls)

    def test_event_without_impl_rejected(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"p.py": PLUGIN_EVENT_NO_IMPL})
        cls = extract_plugin_class(load_module(os.path.join(pdir, "p.py")), "p.py")
        assert cls is not None
        assert not validate_plugin(cls)

    def test_import_error_isolated(self, tmp_path):
        """单个插件加载失败不影响其他插件"""
        pdir = _write_plugins(tmp_path, {
            "bad.py": PLUGIN_IMPORT_ERROR,
            "good.py": PLUGIN_VALID_EVENT,
        })
        classes = load_plugins([
            os.path.join(pdir, "bad.py"),
            os.path.join(pdir, "good.py"),
        ])
        names = [c.name for c in classes]
        assert "valid_event" in names
        assert "bad" not in names


class TestRegistry:
    def test_load_and_query(self, tmp_path):
        pdir = _write_plugins(tmp_path, {
            "a.py": PLUGIN_VALID_EVENT,
            "b.py": PLUGIN_VALID_POLL,
        })
        reg = PluginRegistry([
            os.path.join(pdir, "a.py"),
            os.path.join(pdir, "b.py"),
        ])
        assert reg.has("valid_event")
        assert reg.has("valid_poll")
        assert not reg.has("nope")
        inst = reg.instantiate("valid_event")
        assert inst is not None and inst.name == "valid_event"
        assert reg.instantiate("nope") is None

    def test_duplicate_name_skipped(self, tmp_path):
        pdir = tmp_path / "plugins"
        pdir.mkdir()
        (pdir / "x").mkdir()
        (pdir / "x" / "__init__.py").write_text(PLUGIN_VALID_EVENT.replace("valid_event", "dup"))
        (pdir / "y.py").write_text(PLUGIN_VALID_EVENT.replace("valid_event", "dup"))
        reg = PluginRegistry([
            os.path.join(str(pdir), "x"),
            os.path.join(str(pdir), "y.py"),
        ])
        assert reg.has("dup")
        count = sum(1 for p in reg.list_all() if p["name"] == "dup")
        assert count == 1

    def test_list_all_fields(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"a.py": PLUGIN_AUTO_LOAD})
        reg = PluginRegistry([os.path.join(pdir, "a.py")])
        items = reg.list_all()
        assert len(items) == 1
        item = items[0]
        assert item["name"] == "autoload"
        assert item["triggers"] == ["event"]
        assert item["autoLoad"] is True

    def test_auto_load_match_command(self):
        rule = {"command": r"python|pip"}
        assert _match_auto_load(rule, "python -u -i", None, None)
        assert _match_auto_load(rule, ["python", "-u"], None, None)
        assert not _match_auto_load(rule, "cmd.exe /c dir", None, None)

    def test_auto_load_match_command_keywords(self):
        rule = {"command": ["python", "uv"]}
        assert _match_auto_load(rule, "uv pip install", None, None)
        assert not _match_auto_load(rule, "node app.js", None, None)

    def test_auto_load_match_cwd_prefix_and_regex(self):
        rule = {"cwd": [r"^/data/", "/projects"]}
        assert _match_auto_load(rule, None, "/data/foo", None)
        assert _match_auto_load(rule, None, "/projects/x", None)
        assert not _match_auto_load(rule, None, "/tmp", None)

    def test_auto_load_match_env(self):
        rule = {"env": {"VIRTUAL_ENV": r"venv", "FOO": ""}}
        assert _match_auto_load(rule, None, None, {"VIRTUAL_ENV": "/x/venv", "FOO": "1"})
        assert not _match_auto_load(rule, None, None, {"VIRTUAL_ENV": "/x"})
        assert not _match_auto_load(rule, None, None, {"VIRTUAL_ENV": "/x/venv"})

    def test_auto_load_all_dimensions_required(self):
        rule = {"command": r"python", "cwd": ["/data"]}
        assert _match_auto_load(rule, "python x.py", "/data/a", None)
        assert not _match_auto_load(rule, "python x.py", "/tmp", None)

    def test_auto_load_exception_is_safe(self, tmp_path):
        """规则判定异常视为不命中，不影响其他插件"""
        pdir = _write_plugins(tmp_path, {"a.py": PLUGIN_AUTO_LOAD})
        reg = PluginRegistry([os.path.join(pdir, "a.py")])
        hits = reg.match_auto_load(42, None, None)
        assert hits == []


# ── auto_load 声明结构校验（拼写错误/类型错误在加载期拒绝） ──────

def _autoload_plugin(rule_decl):
    """生成带指定 auto_load 声明的插件源码"""
    return (
        "from src.plugins.base import Plugin\n"
        "class P(Plugin):\n"
        "    name = 'al'\n"
        "    triggers = ['event']\n"
        "    auto_load = %s\n"
        "    def on_event(self, ctx, event): pass\n"
        "plugin = P()\n" % rule_decl
    )


class TestAutoLoadValidation:
    """auto_load 结构校验：非 dict / 未知键 / 维度类型错误均在加载期拒绝"""

    def test_non_dict_rejected(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"a.py": _autoload_plugin("'python'")})
        cls = extract_plugin_class(load_module(os.path.join(pdir, "a.py")), "a.py")
        assert cls is not None
        assert not validate_plugin(cls)

    def test_list_rejected(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"a.py": _autoload_plugin("['python']")})
        cls = extract_plugin_class(load_module(os.path.join(pdir, "a.py")), "a.py")
        assert not validate_plugin(cls)

    def test_unknown_key_rejected(self, tmp_path):
        # 拼写错误（cmd 而非 command）：未知键在加载期拒绝，
        # 避免 _match_auto_load 跳过所有维度而"匹配一切"
        pdir = _write_plugins(tmp_path, {"a.py": _autoload_plugin("{'cmd': 'python'}")})
        cls = extract_plugin_class(load_module(os.path.join(pdir, "a.py")), "a.py")
        assert not validate_plugin(cls)

    def test_command_wrong_type_rejected(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"a.py": _autoload_plugin("{'command': 42}")})
        cls = extract_plugin_class(load_module(os.path.join(pdir, "a.py")), "a.py")
        assert not validate_plugin(cls)

    def test_cwd_wrong_type_rejected(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"a.py": _autoload_plugin("{'cwd': '/data'}")})
        cls = extract_plugin_class(load_module(os.path.join(pdir, "a.py")), "a.py")
        assert not validate_plugin(cls)

    def test_env_wrong_type_rejected(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"a.py": _autoload_plugin("{'env': []}")})
        cls = extract_plugin_class(load_module(os.path.join(pdir, "a.py")), "a.py")
        assert not validate_plugin(cls)

    def test_valid_autoload_accepted(self, tmp_path):
        rule = "{'command': r'python', 'cwd': ['/data'], 'env': {'V': ''}}"
        pdir = _write_plugins(tmp_path, {"a.py": _autoload_plugin(rule)})
        cls = extract_plugin_class(load_module(os.path.join(pdir, "a.py")), "a.py")
        assert cls is not None
        assert validate_plugin(cls)

    def test_unknown_key_not_silently_match_all(self, tmp_path):
        """未知键的插件被拒绝加载，不会注入所有会话"""
        pdir = _write_plugins(tmp_path, {"a.py": _autoload_plugin("{'cmd': 'python'}")})
        reg = PluginRegistry([os.path.join(pdir, "a.py")])
        assert not reg.has("al")
        # 任意 exec 请求都不命中
        assert reg.match_auto_load("totally unrelated", None, None) == []
        assert reg.match_auto_load("", None, None) == []