"""src/client/cli_plugins.py 单测 — CliPluginHost 三钩子链、过滤、异常隔离"""

import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)

from src.client.cli_plugins import CliPluginHost  # noqa: E402
from src.plugins.base import Plugin  # noqa: E402


class FakeClient:
    name = "fake-client"


class LoggingCliPlugin(Plugin):
    """记录钩子调用，可配置各钩子行为"""

    kind = "cli"
    commands = []

    def __init__(self, name="lp", render="R", transform=None, before=None):
        self.name = name
        self._render = render
        self._transform = transform
        self._before = before
        self.calls = []

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
    commands = ["exec", "send"]


class CrashPlugin(Plugin):
    kind = "cli"

    def before_request(self, ctx, msg):
        raise RuntimeError("boom")

    def transform_response(self, ctx, resp):
        raise RuntimeError("boom")

    def render_response(self, ctx, resp):
        raise RuntimeError("boom")


_TEST_PATHS = [os.path.join(_PROJECT_ROOT, "config", "plugins", "simple")]


def _activate_all(host):
    """挂载宿主内全部插件（模拟 exec --plugin / 会话挂载后的自动挂钩）"""
    host.activate([p.name for p in host._plugins])


class TestLoading:
    def test_loads_cli_plugins_from_paths(self):
        host = CliPluginHost(_TEST_PATHS)
        assert host.names() == ["simple"]

    def test_load_skips_non_cli_kind(self):
        paths = [
            os.path.join(_PROJECT_ROOT, "config", "plugins", "files"),
            os.path.join(_PROJECT_ROOT, "config", "plugins", "state_check"),
        ]
        host = CliPluginHost(paths)
        assert host.is_empty()

    def test_empty_paths(self):
        assert CliPluginHost([]).is_empty()

    def test_activate_gates_hooks(self):
        """未挂载的插件不参与钩子链；activate 后自动派发"""
        host = CliPluginHost([], FakeClient())
        host._plugins = [LoggingCliPlugin(name="a", render="R")]
        assert host.render_response("exec", {}) is None
        _activate_all(host)
        assert host.render_response("exec", {}) == "R"


class TestBeforeRequest:
    def test_none_passthrough(self):
        host = CliPluginHost([])
        host._plugins = [LoggingCliPlugin(name="a", before=lambda m: None)]
        _activate_all(host)
        msg = {"type": "exec"}
        assert host.before_request("exec", msg) is msg

    def test_dict_replaces(self):
        host = CliPluginHost([], FakeClient())
        host._plugins = [LoggingCliPlugin(name="a", before=lambda m: dict(m, extra=1))]
        _activate_all(host)
        assert host.before_request("exec", {"type": "exec"}) == {"type": "exec", "extra": 1}

    def test_chain_order(self):
        host = CliPluginHost([], FakeClient())
        host._plugins = [
            LoggingCliPlugin(name="a", before=lambda m: dict(m, a=1)),
            LoggingCliPlugin(name="b"),
            LoggingCliPlugin(name="c", before=lambda m: dict(m, c=2)),
        ]
        _activate_all(host)
        result = host.before_request("exec", {"type": "exec"})
        assert result == {"type": "exec", "a": 1, "c": 2}

    def test_exception_isolated(self):
        host = CliPluginHost([], FakeClient())
        host._plugins = [CrashPlugin(), LoggingCliPlugin(name="ok", before=lambda m: dict(m, ok=1))]
        _activate_all(host)
        assert host.before_request("exec", {"type": "x"}) == {"type": "x", "ok": 1}


class TestTransformResponse:
    def test_chain_replaces(self):
        host = CliPluginHost([], FakeClient())
        host._plugins = [
            LoggingCliPlugin(name="a", transform=lambda r: dict(r, a=1)),
            LoggingCliPlugin(name="b"),
        ]
        _activate_all(host)
        resp = {"type": "result"}
        assert host.transform_response("exec", resp) == {"type": "result", "a": 1}

    def test_exception_isolated(self):
        host = CliPluginHost([], FakeClient())
        host._plugins = [CrashPlugin(), LoggingCliPlugin(name="ok", transform=lambda r: dict(r, ok=1))]
        _activate_all(host)
        assert host.transform_response("exec", {"type": "x"}) == {"type": "x", "ok": 1}


class TestRenderResponse:
    def test_first_non_none_wins(self):
        host = CliPluginHost([], FakeClient())
        host._plugins = [
            LoggingCliPlugin(name="a", render=None),
            LoggingCliPlugin(name="b", render="text-b"),
            LoggingCliPlugin(name="c", render="text-c"),
        ]
        _activate_all(host)
        assert host.render_response("exec", {"type": "result"}) == "text-b"

    def test_all_none_returns_none(self):
        host = CliPluginHost([], FakeClient())
        host._plugins = [LoggingCliPlugin(name="a", render=None)]
        _activate_all(host)
        assert host.render_response("exec", {"type": "result"}) is None

    def test_empty_chain(self):
        assert CliPluginHost([]).render_response("exec", {}) is None

    def test_exception_isolated(self):
        host = CliPluginHost([], FakeClient())
        host._plugins = [CrashPlugin(), LoggingCliPlugin(name="ok", render="text")]
        _activate_all(host)
        assert host.render_response("exec", {}) == "text"


class TestCommandScope:
    def test_scoped_plugin_skipped_outside_commands(self):
        host = CliPluginHost([], FakeClient())
        scoped = ScopedPlugin(name="sc")
        host._plugins = [scoped]
        _activate_all(host)

        scoped.calls.clear()
        assert host.render_response("events", {"type": "result"}) is None
        assert host.render_response("exec", {"type": "result"}) == "R"
        assert [c[1] for c in scoped.calls] == ["exec"]

    def test_empty_commands_means_all(self):
        host = CliPluginHost([], FakeClient())
        host._plugins = [LoggingCliPlugin(name="all", render="R")]
        _activate_all(host)
        assert host.render_response("list", {}) == "R"


class TestRenderHook:
    def test_uses_last_command(self):
        host = CliPluginHost([], FakeClient())
        scoped = ScopedPlugin(name="sc")
        host._plugins = [scoped]
        _activate_all(host)
        host.before_request("exec", {"type": "exec"})
        assert host.render_hook({"type": "result"}) == "R"
        host.before_request("events", {"type": "events"})
        assert host.render_hook({"type": "result"}) is None

    def test_last_command_default_empty(self):
        host = CliPluginHost([], FakeClient())
        host._plugins = [LoggingCliPlugin(name="all", render="R")]
        _activate_all(host)
        assert host.render_hook({"type": "x"}) == "R"