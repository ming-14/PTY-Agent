"""plugin 命令 handler 单测（daemon 消息层：list/ls/attach/detach/cmd）"""

import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.daemon.handlers.base import HandlerContext
from src.daemon.handlers.plugin_handler import PluginHandler
from src.plugins.base import Plugin
from src.plugins.registry import PluginRegistry
from src.session.manager import SessionManager


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


class EchoPlugin(Plugin):
    name = "echo"
    triggers = ["event"]
    description = "echo plugin"

    def on_event(self, ctx, event):
        pass

    def handle_command(self, ctx, msg):
        return {"handled": True, "command": msg.get("command")}


def _send(handler, ctx, msg):
    conn = _FakeConn()
    handler.handle(ctx, conn, msg)
    assert conn.sent
    return json.loads(conn.sent[-1].decode("utf-8"))


@pytest.fixture
def manager(tmp_path):
    """真实 SessionManager + 临时插件注册表（插件类注入，不落盘）"""
    reg = PluginRegistry([])
    reg._classes["echo"] = EchoPlugin
    return SessionManager(plugin_registry=reg)


@pytest.fixture
def ctx(manager):
    return HandlerContext(manager, None, None)


class TestPluginHandler:
    def test_list(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "list"})
        assert resp["action"] == "list"
        assert resp["plugins"] == [{
            "name": "echo", "version": "1.0", "description": "echo plugin",
            "triggers": ["event"], "pollInterval": None, "autoLoad": False,
            "messageTypes": [], "needsIO": False,
        }]

    def test_list_when_disabled(self, tmp_path):
        manager = SessionManager(plugin_registry=None)
        ctx = HandlerContext(manager, None, None)
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "list"})
        assert resp["type"] == "error"

    def test_ls_empty_and_unknown_session(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "ls", "id": "nope"})
        assert resp["type"] == "error"
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "ls", "id": "x"})
        assert resp["type"] == "error"

    def test_attach_detach_unknown_plugin(self, ctx, manager, tmp_path):
        session = manager.create_session("s1", "python -u -i", cwd=str(tmp_path))
        try:
            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "attach",
                                                "id": "s1", "name": "ghost"})
            assert resp["type"] == "error"
        finally:
            session.stop()

    def test_attach_detach_lifecycle(self, ctx, manager, tmp_path):
        session = manager.create_session("s1", "python -u -i", cwd=str(tmp_path))
        try:
            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "attach",
                                                "id": "s1", "name": "echo"})
            assert resp.get("type") != "error"
            assert resp["commandType"] == "plugin"
            assert resp["plugins"] == [{"name": "echo", "version": "1.0"}]
            assert session.plugin_host.names() == ["echo"]

            # 重复 attach 报错
            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "attach",
                                                "id": "s1", "name": "echo"})
            assert resp["type"] == "error"

            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "ls", "id": "s1"})
            assert resp["plugins"] == [{"name": "echo", "version": "1.0"}]

            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "detach",
                                                "id": "s1", "name": "echo"})
            assert resp.get("type") != "error"
            assert resp["plugins"] == []
            assert session.plugin_host.is_empty()
        finally:
            session.stop()

    def test_cmd_route(self, ctx, manager, tmp_path):
        session = manager.create_session("s1", "python -u -i", cwd=str(tmp_path))
        try:
            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "cmd",
                                                "id": "s1", "name": "echo",
                                                "command": "status"})
            assert resp["type"] == "error"  # 未挂载

            session.plugin_host.attach(EchoPlugin())
            resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "cmd",
                                                "id": "s1", "name": "echo",
                                                "command": "status"})
            assert resp["result"] == {"handled": True, "command": "status"}
        finally:
            session.stop()

    def test_unknown_action(self, ctx):
        resp = _send(PluginHandler(), ctx, {"type": "plugin", "action": "nope"})
        assert resp["type"] == "error"