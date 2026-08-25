"""config/plugins/2048 插件单测 — before_request 改写钩子（exec 拦截）"""

import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, _PROJECT_ROOT)

from src.client.cli_plugins import CliContext  # noqa: E402
from src.plugins.loader import load_plugin_dir  # noqa: E402

_PLUGIN_PATH = os.path.join(_PROJECT_ROOT, "config", "plugins", "2048")


@pytest.fixture(scope="module")
def plugin_cls():
    assert os.path.exists(_PLUGIN_PATH), "2048 目录不在 config/plugins/ 中"
    loaded = load_plugin_dir(_PLUGIN_PATH)
    assert loaded is not None
    assert loaded.manifest.kind == "cli"
    assert loaded.manifest.id == "2048"
    return loaded.cls


@pytest.fixture
def plugin(plugin_cls):
    return plugin_cls()


def _ctx(plugin, config=None):
    return CliContext("exec", None, plugin, config=config)


class _StubConfig:
    """插件配置视图替身：仅暴露 get（插件只经 config.get 读配置）"""

    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def _exec_msg(overrides=None):
    """构造 exec 请求消息（模拟客户端 cmd_exec 组装后的 msg 格式）"""
    msg = {
        "type": "exec",
        "id": "game2048",
        "command": ["echo", "hello"],
        "timeout": None,
        "cwd": "/tmp",
    }
    if overrides:
        msg.update(overrides)
    return msg


class TestBeforeRequest:
    def test_rewrites_exec_command(self, plugin):
        """exec 请求的 command 被改写为 [interpreter, main.py]"""
        msg = _exec_msg()
        result = plugin.before_request(_ctx(plugin), msg)
        assert result is not None
        cmd = result["command"]
        assert isinstance(cmd, list)
        assert len(cmd) == 2
        assert cmd[0] == sys.executable
        assert cmd[1].endswith(os.path.join("2048", "main.py"))

    def test_sets_timeout_and_trigger(self, plugin):
        """改写后 timeout=10, trigger='quit', explicit_timeout=True"""
        msg = _exec_msg()
        result = plugin.before_request(_ctx(plugin), msg)
        assert result["timeout"] == 10
        assert result["trigger"] == "quit"
        assert result["explicit_timeout"] is True

    def test_preserves_session_id(self, plugin):
        """sid 保留不变"""
        msg = _exec_msg({"id": "mimo"})
        result = plugin.before_request(_ctx(plugin), msg)
        assert result["id"] == "mimo"

    def test_non_exec_returns_none(self, plugin):
        """非 exec 请求（send/read/mouse）放行，返回 None"""
        for t in ("send", "read", "mouse"):
            msg = {"type": t, "id": "s1"}
            assert plugin.before_request(_ctx(plugin), msg) is None

    def test_config_overrides(self, plugin):
        """config 配置覆盖 interpreter/timeout/trigger"""
        config = _StubConfig(
            {
                "interpreter": "python3",
                "timeout": 30,
                "trigger": "GAME_OVER",
            }
        )
        msg = _exec_msg()
        result = plugin.before_request(_ctx(plugin, config=config), msg)
        assert result["command"][0] == "python3"
        assert result["timeout"] == 30
        assert result["trigger"] == "GAME_OVER"

    def test_empty_config_uses_defaults(self, plugin):
        """config 缺省时使用插件模块默认值"""
        msg = _exec_msg()
        result = plugin.before_request(_ctx(plugin), msg)
        assert result["timeout"] == 10
        assert result["trigger"] == "quit"
        assert result["explicit_timeout"] is True