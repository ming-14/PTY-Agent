"""plugin 命令 handler 单测（daemon 消息层：list/ls/attach/detach/cmd + 生命周期）"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.execution.context import HandlerContext
from src.daemon.handlers.plugin_handler import PluginHandler
from src.plugins.registry import PluginRegistry
from src.session.manager import SessionManager
from tests.helpers import write_plugin_dir


class _FakeConn:
    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        pass

    def fileno(self):
        return -1

    def settimeout(self, t):
        pass


ECHO_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def on_event(self, ctx, event): pass\n"
    "    def handle_command(self, ctx, msg):\n"
    "        return {'handled': True, 'command': msg.get('command')}\n"
    "plugin = P()\n"
)


def _send(handler, ctx, msg):
    conn = _FakeConn()
    handler.handle(ctx, conn, msg)
    assert conn.sent
    return json.loads(conn.sent[-1].decode("utf-8"))


@pytest.fixture
def manager(tmp_path):
    """真实 SessionManager + 临时插件注册表（清单目录加载）"""
    pdir = write_plugin_dir(tmp_path, "echo", "session", ECHO_SRC)
    reg = PluginRegistry([pdir], states={}, enabled_by_default=True)
    return SessionManager(plugin_registry=reg)


@pytest.fixture
def ctx(manager):
    return HandlerContext(manager, None, None)


class TestListAndLs:
    def test_list(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "list"})
        assert resp["action"] == "list"
        assert resp["plugins"][0]["name"] == "echo"
        assert resp["plugins"][0]["kind"] == "session"
        assert resp["plugins"][0]["state"] == "enabled"

    def test_list_when_disabled(self):
        manager = SessionManager(plugin_registry=None)
        ctx = HandlerContext(manager, None, None)
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "list"})
        assert resp["type"] == "error"

    def test_ls_empty_and_unknown_session(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "ls", "id": "nope"})
        assert resp["type"] == "error"


class TestAttachDetach:
    def test_attach_detach_unknown_plugin(self, ctx, manager, tmp_path):
        session = manager.create_session("s1", [sys.executable, "-u", "-i"], cwd=str(tmp_path))
        try:
            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "attach",
                                                "id": "s1", "name": "ghost"})
            assert resp["type"] == "error"
        finally:
            session.stop()

    def test_attach_detach_lifecycle(self, ctx, manager, tmp_path):
        session = manager.create_session("s1", [sys.executable, "-u", "-i"], cwd=str(tmp_path))
        try:
            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "attach",
                                                "id": "s1", "name": "echo"})
            assert resp.get("type") != "error"
            assert resp["commandType"] == "plugin"
            assert resp["plugins"] == [{"name": "echo", "version": "1.0", "options": {}}]
            assert session.plugin_host.names() == ["echo"]

            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "attach",
                                                "id": "s1", "name": "echo"})
            assert resp["type"] == "error"

            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "ls", "id": "s1"})
            assert resp["plugins"] == [{"name": "echo", "version": "1.0", "options": {}}]

            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "detach",
                                                "id": "s1", "name": "echo"})
            assert resp.get("type") != "error"
            assert resp["plugins"] == []
            assert session.plugin_host.is_empty()
        finally:
            session.stop()

    def test_cmd_route(self, ctx, manager, tmp_path):
        session = manager.create_session("s1", [sys.executable, "-u", "-i"], cwd=str(tmp_path))
        try:
            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "cmd",
                                                "id": "s1", "name": "echo",
                                                "command": "status"})
            assert resp["type"] == "error"  # 未挂载

            inst = manager.plugin_registry.instantiate("echo")
            assert session.plugin_host.attach(inst)
            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "cmd",
                                                "id": "s1", "name": "echo",
                                                "command": "status"})
            assert resp["result"] == {"handled": True, "command": "status"}
        finally:
            session.stop()

    def test_ls_includes_cli_plugins(self, ctx, manager, tmp_path):
        """exec 记录的 CLI 插件在 plugin ls 中回显（客户端据此自动挂钩）"""
        session = manager.create_session(
            "s1", [sys.executable, "-u", "-i"], cwd=str(tmp_path), cli_plugins=["ai"]
        )
        try:
            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "ls", "id": "s1"})
            assert resp["plugins"] == [{"name": "ai", "version": "", "cli": True}]
        finally:
            session.stop()


class TestLifecycleActions:
    def test_enable_disable(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "disable",
                                            "name": "echo"})
        assert resp.get("type") != "error"
        assert "已禁用" in resp["message"]
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "list"})
        assert resp["plugins"][0]["state"] == "disabled"
        # 禁用后不可挂载
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "enable",
                                            "name": "echo"})
        assert resp.get("type") != "error"
        assert "已启用" in resp["message"]

    def test_disable_unknown(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "disable",
                                            "name": "ghost"})
        assert resp["type"] == "error"

    def test_reload(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "reload",
                                            "name": "echo"})
        assert resp.get("type") != "error"
        assert "已重载" in resp["message"]

    def test_info(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "info",
                                            "name": "echo"})
        assert resp.get("type") != "error"
        info = resp["info"]
        assert info["name"] == "echo"
        assert info["kind"] == "session"
        assert info["state"] == "enabled"
        assert info["permissions"] == []

    def test_info_unknown(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "info",
                                            "name": "ghost"})
        assert resp["type"] == "error"

    def test_status(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "status",
                                            "name": "echo"})
        assert resp.get("type") != "error"
        assert resp["info"]["state"] == "enabled"

    def test_config_get(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "config",
                                            "name": "echo", "sub": "get"})
        assert resp.get("type") != "error"
        assert resp["config"] == {}

    def test_config_set_and_get(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "config",
                                            "name": "echo", "sub": "set",
                                            "key": "timeout", "value": "30"})
        assert resp.get("type") != "error"
        assert "已更新" in resp["message"]
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "config",
                                            "name": "echo", "sub": "get"})
        assert resp["config"] == {"timeout": 30}

    def test_config_invalid_sub(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "config",
                                            "name": "echo", "sub": "nope"})
        assert resp["type"] == "error"

    def test_unknown_action(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "nope"})
        assert resp["type"] == "error"