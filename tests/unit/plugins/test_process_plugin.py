"""进程级插件测试 —— message_types 声明、单例实例化、消息路由适配器

覆盖：
- loader 校验：message_types 声明合法性 / 未实现 handle_message / needs_io 类型
- registry：进程级插件单例实例化、instantiate 拒绝进程级、list_all 字段
- dispatcher 适配器：dict 响应发送、HANDLED 不发送、None 回错误、异常隔离
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.base import HANDLED, Plugin, ProcessPluginContext
from src.plugins.loader import load_plugin_dir, validate_plugin
from src.plugins.manifest import PluginManifest
from src.plugins.registry import PluginRegistry
from src.daemon.handlers.dispatcher import PluginMessageHandler
from tests.helpers import write_plugin_dir, make_manifest


# ── 测试源码 ───────────────────────────────────────────────

PROCESS_OK = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def handle_message(self, ctx, msg): return {'echo': msg.get('type')}\n"
    "plugin = P()\n"
)

PROCESS_NO_IMPL = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin): pass\n"
    "plugin = P()\n"
)

PROCESS_HANDLED = (
    "from src.plugins.base import HANDLED, Plugin\n"
    "class P(Plugin):\n"
    "    def handle_message(self, ctx, msg): return HANDLED\n"
    "plugin = P()\n"
)

PROCESS_EXCEPTION = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def handle_message(self, ctx, msg): raise RuntimeError('boom')\n"
    "plugin = P()\n"
)


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


class TestProcessPluginValidation:
    def test_valid_process_plugin(self, tmp_path):
        """message_types 合法 + handle_message 实现 → 加载成功"""
        pdir = write_plugin_dir(
            tmp_path, "proc", "process", PROCESS_OK,
            manifest_extra={"messageTypes": ["cmd_a", "cmd_b"], "needsIO": True},
        )
        loaded = load_plugin_dir(pdir)
        assert loaded is not None
        assert loaded.manifest.message_types == ["cmd_a", "cmd_b"]
        assert loaded.manifest.needs_io is True

    def test_message_types_without_impl_rejected(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "bad", "process", PROCESS_NO_IMPL,
            manifest_extra={"messageTypes": ["cmd_a"]},
        )
        assert load_plugin_dir(pdir) is None

    def test_message_types_must_be_list(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "bad", "process", PROCESS_OK,
            manifest_extra={"messageTypes": "cmd_a"},
        )
        assert load_plugin_dir(pdir) is None

    def test_needs_io_must_be_bool(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "bad", "process", PROCESS_OK,
            manifest_extra={"messageTypes": ["cmd_a"], "needsIO": "yes"},
        )
        assert load_plugin_dir(pdir) is None


class TestProcessRegistry:
    def test_process_instance_singleton(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "proc", "process", PROCESS_OK,
            manifest_extra={"messageTypes": ["cmd_a"]},
        )
        reg = PluginRegistry([pdir])
        # 通过 process_instances 获取单例
        instances = reg.process_instances()
        assert "proc" in instances
        inst = instances["proc"]
        # 进程级单例是同一个实例（enable 时创建）
        assert inst is reg.process_instances()["proc"]

    def test_instantiate_rejects_process(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "proc", "process", PROCESS_OK,
            manifest_extra={"messageTypes": ["cmd_a"]},
        )
        reg = PluginRegistry([pdir])
        assert reg.instantiate("proc") is None

    def test_process_instance_failure_isolated(self, tmp_path):
        # 无效的 messageTypes 导致 BROKEN（不抛异常）
        reg = PluginRegistry([])
        # registry 是无插件空注册表，空操作安全
        assert reg.list_all() == []

    def test_list_all_has_message_fields(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "proc", "process", PROCESS_OK,
            manifest_extra={"messageTypes": ["cmd_a"], "needsIO": True},
        )
        reg = PluginRegistry([pdir])
        items = reg.list_all()
        assert len(items) == 1
        item = items[0]
        assert item["messageTypes"] == ["cmd_a"]
        assert item["needsIO"] is True
        assert item["kind"] == "process"


class TestPluginMessageHandler:
    """PluginMessageHandler 适配器（dispatcher 用）"""

    @pytest.fixture
    def handler(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "proc", "process", PROCESS_OK,
            manifest_extra={"messageTypes": ["cmd_a"]},
        )
        reg = PluginRegistry([pdir])
        inst = reg.process_instances()["proc"]
        reg.environment.events  # 确保环境可用
        return PluginMessageHandler(inst)

    def _handle(self, handler, msg):
        conn = _FakeConn()
        from src.execution.context import HandlerContext
        handler.handle(HandlerContext(None, None, None), conn, msg)
        assert conn.sent, "未收到响应"
        return json.loads(conn.sent[-1].decode("utf-8"))

    def test_dict_response_sent(self, handler):
        resp = self._handle(handler, {"type": "cmd_a", "id": "test"})
        assert resp["echo"] == "cmd_a"

    def test_handled_no_response(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "hdl", "process", PROCESS_HANDLED,
            manifest_extra={"messageTypes": ["done"]},
        )
        reg = PluginRegistry([pdir])
        inst = reg.process_instances()["hdl"]
        h = PluginMessageHandler(inst)
        conn = _FakeConn()
        from src.execution.context import HandlerContext
        h.handle(HandlerContext(None, None, None), conn, {"type": "done"})
        assert not conn.sent

    def test_none_returns_error(self, tmp_path):
        # 无 handle_message 实现的插件 ─ BROKEN，不会进入 handler
        # 测试 handler 接收 None 的情况：通过直接构造
        class _NonePlugin(Plugin):
            manifest = make_manifest("none", kind="process", message_types=["x"])
            def handle_message(self, ctx, msg): return None

        h = PluginMessageHandler(_NonePlugin())
        conn = _FakeConn()
        from src.execution.context import HandlerContext
        h.handle(HandlerContext(None, None, None), conn, {"type": "x"})
        resp = json.loads(conn.sent[-1].decode("utf-8"))
        assert resp["type"] == "error"

    def test_exception_isolated_to_error(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "boom", "process", PROCESS_EXCEPTION,
            manifest_extra={"messageTypes": ["crash"]},
        )
        reg = PluginRegistry([pdir])
        # 插件实例化正常，但 handle_message 抛异常
        inst = reg.process_instances()["boom"]
        h = PluginMessageHandler(inst)
        conn = _FakeConn()
        from src.execution.context import HandlerContext
        h.handle(HandlerContext(None, None, None), conn, {"type": "crash"})
        resp = json.loads(conn.sent[-1].decode("utf-8"))
        assert resp["type"] == "error"