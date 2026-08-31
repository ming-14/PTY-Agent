"""插件加载器单测 — 清单驱动加载、导出约定、声明校验、失败隔离"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.loader import (  # noqa: E402
    extract_plugin_class,
    load_plugin_dir,
    load_plugins,
    module_name,
    validate_plugin,
)
from src.plugins.manifest import PluginManifest  # noqa: E402
from tests.helpers import write_plugin_dir  # noqa: E402


SESSION_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def on_event(self, ctx, event): pass\n"
    "plugin = P()\n"
)

EVENT_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def on_event(self, ctx, event): pass\n"
    "plugin = P\n"
)

PROCESS_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def handle_message(self, ctx, msg): return {'ok': True}\n"
    "plugin = P()\n"
)


class TestLoadPluginDir:
    def test_loads_session_plugin(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        loaded = load_plugin_dir(pdir)
        assert loaded is not None
        assert loaded.manifest.id == "demo"
        assert loaded.cls.name == "demo"
        assert loaded.cls.kind == ["session"]

    def test_loads_process_plugin(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "proc", "process", PROCESS_SRC,
            manifest_extra={"messageTypes": ["cmd_a"]},
        )
        loaded = load_plugin_dir(pdir)
        assert loaded is not None
        assert loaded.cls.manifest.message_types == ["cmd_a"]

    def test_module_name_unique(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        assert load_plugin_dir(pdir) is not None
        loaded = load_plugin_dir(pdir)
        assert loaded is not None
        assert module_name("demo") in sys.modules

    def test_missing_entry_file(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        os.remove(os.path.join(pdir, "__init__.py"))
        assert load_plugin_dir(pdir) is None

    def test_missing_plugin_export(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "demo", "session", "x = 1\n",
        )
        assert load_plugin_dir(pdir) is None

    def test_bad_export_type(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "demo", "session", "plugin = 42\n",
        )
        assert load_plugin_dir(pdir) is None

    def test_broken_module_skipped(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "demo", "session", "raise RuntimeError('boom')\n",
        )
        assert load_plugin_dir(pdir) is None


class TestDeclarationValidation:
    def _cls(self, tmp_path, plugin_id, kind, src, extra=None):
        pdir = write_plugin_dir(tmp_path, plugin_id, kind, src, manifest_extra=extra)
        loaded = load_plugin_dir(pdir)
        assert loaded is not None, "前置条件：插件应可加载"
        return loaded.cls, loaded.manifest

    def test_event_declared_but_not_implemented(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "bad", "session",
            "from src.plugins.base import Plugin\n"
            "class P(Plugin): pass\n"
            "plugin = P()\n",
            manifest_extra={"triggers": ["event"]},
        )
        assert load_plugin_dir(pdir) is None

    def test_poll_declared_but_not_implemented(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "bad", "session",
            "from src.plugins.base import Plugin\n"
            "class P(Plugin): pass\n"
            "plugin = P()\n",
            manifest_extra={"triggers": ["poll"], "pollInterval": 1.0},
        )
        assert load_plugin_dir(pdir) is None

    def test_message_types_declared_but_not_implemented(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "bad", "process",
            "from src.plugins.base import Plugin\n"
            "class P(Plugin): pass\n"
            "plugin = P()\n",
            manifest_extra={"messageTypes": ["cmd_a"]},
        )
        assert load_plugin_dir(pdir) is None

    def test_cli_requires_cli_hook(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "bad", "cli",
            "from src.plugins.base import Plugin\n"
            "class P(Plugin): pass\n"
            "plugin = P()\n",
        )
        assert load_plugin_dir(pdir) is None

    def test_cli_with_hook_ok(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "good", "cli",
            "from src.plugins.base import Plugin\n"
            "class P(Plugin):\n"
            "    def render_response(self, ctx, resp): return 'x'\n"
            "plugin = P()\n",
        )
        assert load_plugin_dir(pdir) is not None

    def test_hooks_declared_but_not_implemented(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "bad", "session",
            "from src.plugins.base import Plugin\n"
            "class P(Plugin): pass\n"
            "plugin = P()\n",
            manifest_extra={"hooks": {"inspect_state": {}}},
        )
        assert load_plugin_dir(pdir) is None

    def test_hooks_unknown_name(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "bad", "session",
            "from src.plugins.base import Plugin\n"
            "class P(Plugin): pass\n"
            "plugin = P()\n",
            manifest_extra={"hooks": {"on_nope": {}}},
        )
        assert load_plugin_dir(pdir) is None

    def test_validate_plugin_mismatch(self, tmp_path):
        cls, manifest = self._cls(tmp_path, "demo", "session", SESSION_SRC)
        bad = PluginManifest(
            id="demo", version="1.0", kind="session", path=manifest.path,
            triggers=["event"],
        )
        assert validate_plugin(cls, bad)

    def test_validate_plugin_poll_missing_impl(self, tmp_path):
        cls, manifest = self._cls(tmp_path, "demo", "session", SESSION_SRC)
        bad = PluginManifest(
            id="demo", version="1.0", kind="session", path=manifest.path,
            triggers=["poll"], poll_interval=1.0,
        )
        assert not validate_plugin(cls, bad)


class TestLoadPlugins:
    def test_multiple_dirs_isolation(self, tmp_path):
        good = write_plugin_dir(tmp_path, "good", "session", SESSION_SRC)
        bad = write_plugin_dir(
            tmp_path, "bad", "session", "raise RuntimeError('boom')\n",
        )
        loaded = load_plugins([good, bad])
        assert [l.manifest.id for l in loaded] == ["good"]

    def test_empty_paths(self):
        assert load_plugins([]) == []