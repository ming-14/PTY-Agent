"""src/client/cli_plugins.py 单测 — CliPluginHost 三钩子链、过滤、异常隔离"""

import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)

from src.client.cli_plugins import CliPluginHost  # noqa: E402
from src.plugins.base import Plugin  # noqa: E402
from tests.helpers import make_manifest, write_plugin_dir  # noqa: E402


class FakeClient:
    name = "fake-client"


class LoggingCliPlugin(Plugin):
    def __init__(self, name="lp", render="R", transform=None, before=None,
                 commands=None):
        self.name = name
        self._render = render
        self._transform = transform
        self._before = before
        self.calls = []
        self.manifest = make_manifest(
            name, kind="cli", commands=commands or [],
            hooks={"before_request": {}, "transform_response": {}, "render_response": {}},
        )

    def before_request(self, ctx, msg):
        self.calls.append(("before", ctx.command))
        return self._before(msg) if self._before else None

    def transform_response(self, ctx, resp):
        self.calls.append(("transform", ctx.command))
        return self._transform(resp) if self._transform else None

    def render_response(self, ctx, resp):
        self.calls.append(("render", ctx.command))
        return self._render


class ScopedPlugin(LoggingCliPlugin):
    def __init__(self, name="sc"):
        super().__init__(name=name, commands=["exec", "send"])


class CrashPlugin(Plugin):
    def __init__(self, name="crash"):
        self.name = name
        self.manifest = make_manifest(
            name, kind="cli",
            hooks={"before_request": {}, "transform_response": {}, "render_response": {}},
        )

    def before_request(self, ctx, msg):
        raise RuntimeError("boom")

    def transform_response(self, ctx, resp):
        raise RuntimeError("boom")

    def render_response(self, ctx, resp):
        raise RuntimeError("boom")


_TEST_PATHS = [os.path.join(_PROJECT_ROOT, "config", "plugins", "2048")]


def _set_plugins(host, *plugins):
    """替换宿主插件列表并注册到钩子链"""
    host._plugins = list(plugins)
    for p in plugins:
        host._engine.register(p, p.manifest)
    return host


def _activate_all(host):
    host.activate([p.name for p in host._plugins])


class TestLoading:
    def test_loads_cli_plugins_from_paths(self):
        host = CliPluginHost(_TEST_PATHS)
        assert host.names() == ["2048"]

    def test_load_skips_non_cli_kind(self, tmp_path):
        # 纯 process 形态插件（不含 cli）应被 CliPluginHost 跳过
        plugin_dir = tmp_path / "test_process_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            '{"id":"test-proc","version":"1","kind":"process"}', encoding="utf-8")
        host = CliPluginHost([str(plugin_dir)])
        assert host.is_empty()

    def test_empty_paths(self):
        assert CliPluginHost([]).is_empty()

    def test_kinds_of_dual_kind(self, tmp_path):
        """双形态插件 kinds_of 返回完整形态（reload 分发依据）"""
        pdir = write_plugin_dir(
            tmp_path, "dual", ["process", "cli"],
            "from src.plugins.base import Plugin\n"
            "class P(Plugin):\n"
            "    def render_response(self, ctx, resp):\n"
            "        return None\n"
            "plugin = P\n",
        )
        host = CliPluginHost([pdir])
        assert host.kinds_of("dual") == ["process", "cli"]

    def test_kinds_of_pure_process_not_loaded(self, tmp_path):
        """纯 process 插件不进 CLI 宿主，但 kinds_of 仍能查询（daemon 重载分发用）"""
        pdir = write_plugin_dir(
            tmp_path, "proc", "process",
            "from src.plugins.base import Plugin\nclass P(Plugin):\n    pass\nplugin = P\n",
        )
        host = CliPluginHost([pdir])
        assert host.is_empty()
        assert host.kinds_of("proc") == ["process"]

    def test_kinds_of_unknown(self):
        assert CliPluginHost([]).kinds_of("nope") is None

    def test_activate_gates_hooks(self):
        """未挂载的插件不参与钩子链；activate 后自动派发"""
        host = _set_plugins(CliPluginHost([], FakeClient()), LoggingCliPlugin(name="a", render="R"))
        assert host.render_response("exec", {}) is None
        _activate_all(host)
        assert host.render_response("exec", {}) == "R"


class TestBeforeRequest:
    def test_none_passthrough(self):
        host = _set_plugins(CliPluginHost([]), LoggingCliPlugin(name="a", before=lambda m: None))
        _activate_all(host)
        msg = {"type": "exec"}
        assert host.before_request("exec", msg) is msg

    def test_dict_replaces(self):
        host = _set_plugins(CliPluginHost([], FakeClient()), LoggingCliPlugin(name="a", before=lambda m: dict(m, extra=1)))
        _activate_all(host)
        assert host.before_request("exec", {"type": "exec"}) == {"type": "exec", "extra": 1}

    def test_chain_order(self):
        host = _set_plugins(
            CliPluginHost([], FakeClient()),
            LoggingCliPlugin(name="a", before=lambda m: dict(m, a=1)),
            LoggingCliPlugin(name="b"),
            LoggingCliPlugin(name="c", before=lambda m: dict(m, c=2)),
        )
        _activate_all(host)
        result = host.before_request("exec", {"type": "exec"})
        assert result == {"type": "exec", "a": 1, "c": 2}

    def test_exception_isolated(self):
        host = _set_plugins(
            CliPluginHost([], FakeClient()),
            CrashPlugin(),
            LoggingCliPlugin(name="ok", before=lambda m: dict(m, ok=1)),
        )
        _activate_all(host)
        assert host.before_request("exec", {"type": "x"}) == {"type": "x", "ok": 1}


class TestTransformResponse:
    def test_chain_replaces(self):
        host = _set_plugins(
            CliPluginHost([], FakeClient()),
            LoggingCliPlugin(name="a", transform=lambda r: dict(r, a=1)),
            LoggingCliPlugin(name="b"),
        )
        _activate_all(host)
        resp = {"type": "result"}
        assert host.transform_response("exec", resp) == {"type": "result", "a": 1}

    def test_exception_isolated(self):
        host = _set_plugins(
            CliPluginHost([], FakeClient()),
            CrashPlugin(),
            LoggingCliPlugin(name="ok", transform=lambda r: dict(r, ok=1)),
        )
        _activate_all(host)
        assert host.transform_response("exec", {"type": "x"}) == {"type": "x", "ok": 1}


class TestRenderResponse:
    def test_first_non_none_wins(self):
        host = _set_plugins(
            CliPluginHost([], FakeClient()),
            LoggingCliPlugin(name="a", render=None),
            LoggingCliPlugin(name="b", render="text-b"),
            LoggingCliPlugin(name="c", render="text-c"),
        )
        _activate_all(host)
        assert host.render_response("exec", {"type": "result"}) == "text-b"

    def test_all_none_returns_none(self):
        host = _set_plugins(CliPluginHost([], FakeClient()), LoggingCliPlugin(name="a", render=None))
        _activate_all(host)
        assert host.render_response("exec", {"type": "result"}) is None

    def test_empty_chain(self):
        assert CliPluginHost([]).render_response("exec", {}) is None

    def test_exception_isolated(self):
        host = _set_plugins(
            CliPluginHost([], FakeClient()),
            CrashPlugin(),
            LoggingCliPlugin(name="ok", render="text"),
        )
        _activate_all(host)
        assert host.render_response("exec", {}) == "text"


class TestCommandScope:
    def test_scoped_plugin_skipped_outside_commands(self):
        host = _set_plugins(CliPluginHost([], FakeClient()), ScopedPlugin(name="sc"))
        _activate_all(host)

        scoped = host._plugins[0]
        scoped.calls.clear()
        assert host.render_response("events", {"type": "result"}) is None
        assert host.render_response("exec", {"type": "result"}) == "R"
        assert [c[1] for c in scoped.calls] == ["exec"]

    def test_empty_commands_means_all(self):
        host = _set_plugins(CliPluginHost([], FakeClient()), LoggingCliPlugin(name="all", render="R"))
        _activate_all(host)
        assert host.render_response("list", {}) == "R"


class TestRenderHook:
    def test_uses_last_command(self):
        host = _set_plugins(CliPluginHost([], FakeClient()), ScopedPlugin(name="sc"))
        _activate_all(host)
        host.before_request("exec", {"type": "exec"})
        assert host.render_hook({"type": "result"}) == "R"
        host.before_request("events", {"type": "events"})
        assert host.render_hook({"type": "result"}) is None

    def test_last_command_default_empty(self):
        host = _set_plugins(CliPluginHost([], FakeClient()), LoggingCliPlugin(name="all", render="R"))
        _activate_all(host)
        assert host.render_hook({"type": "x"}) == "R"
