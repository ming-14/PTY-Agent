"""config/plugins/simple 插件单测 — CLI 响应精简（render_response 钩子）"""

import copy
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)

from src.client.cli_plugins import CliContext  # noqa: E402
from src.plugins.loader import extract_plugin_class, load_module, resolve_kind, validate_plugin  # noqa: E402

_PLUGIN_PATH = os.path.join(_PROJECT_ROOT, "config", "plugins", "simple")


@pytest.fixture(scope="module")
def plugin_cls():
    assert os.path.exists(_PLUGIN_PATH), "simple 目录不在 config/plugins/ 中"
    cls = extract_plugin_class(load_module(_PLUGIN_PATH), _PLUGIN_PATH)
    assert cls is not None
    assert validate_plugin(cls)
    assert resolve_kind(cls) == "cli"
    return cls


@pytest.fixture
def plugin(plugin_cls):
    client = type("FakeClient", (), {"name": "c"})()
    return plugin_cls()


def _ctx(plugin):
    return CliContext("exec", None, plugin)


_RAW_RESP = {
    "commandType": "exec",
    "sessionId": "s1",
    "uid": "xxx",
    "outputStream": "hello\nworld",
    "outputOffset": 12,
    "triggerReturnReason": "trigger_matched",
    "program": {
        "rawStartCommand": ["python", "-i"],
        "running": True,
        "ptyType": "wezterm",
        "debugInformation": {"processes": [], "elapsedMs": 3140.0},
    },
    "terminalState": {"state": "Repl", "reason": "repl prompt"},
    "sessionDefaults": {"timeout": 120},
    "hint": "trigger matched",
}


class TestRender:
    def test_output_with_stat_tail(self, plugin):
        text = plugin.render_response(_ctx(plugin), copy.deepcopy(_RAW_RESP))
        assert text == "hello\nworld\n\n---\ntrigger_matched\n3140ms"

    def test_error_response_none(self, plugin):
        assert plugin.render_response(_ctx(plugin), {"type": "error", "message": "boom"}) is None

    def test_no_output_stream_none(self, plugin):
        assert plugin.render_response(_ctx(plugin), {"performed": True, "hint": "done"}) is None

    def test_empty_output_uses_no_output_marker(self, plugin):
        resp = copy.deepcopy(_RAW_RESP)
        resp["outputStream"] = ""
        text = plugin.render_response(_ctx(plugin), resp)
        assert text == "(no output)\n\n---\ntrigger_matched\n3140ms"

    def test_missing_elapsed_omits_ms_line(self, plugin):
        resp = copy.deepcopy(_RAW_RESP)
        del resp["program"]["debugInformation"]
        text = plugin.render_response(_ctx(plugin), resp)
        assert text == "hello\nworld\n\n---\ntrigger_matched"

    def test_missing_reason_keeps_elapsed_line(self, plugin):
        resp = copy.deepcopy(_RAW_RESP)
        resp.pop("triggerReturnReason")
        text = plugin.render_response(_ctx(plugin), resp)
        assert text == "hello\nworld\n\n---\n3140ms"

    def test_missing_all_stat_omits_tail(self, plugin):
        text = plugin.render_response(_ctx(plugin), {"outputStream": "plain"})
        assert text == "plain"