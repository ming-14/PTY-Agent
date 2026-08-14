"""进程级插件测试 —— message_types 声明、单例实例化、消息路由适配器

覆盖：
- loader 校验：message_types 声明合法性 / 未实现 handle_message / needs_io 类型
- registry：进程级插件单例实例化、instantiate 拒绝进程级、list_all 字段
- dispatcher 适配器：dict 响应发送、HANDLED 不发送、None 回错误、异常隔离
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.base import HANDLED, Plugin, ProcessPluginContext
from src.plugins.loader import (
    extract_plugin_class,
    load_module,
    validate_plugin,
)
from src.plugins.registry import PluginRegistry
from src.daemon.handlers.dispatcher import PluginMessageHandler


def _write_plugins(tmp_path, files: dict):
    pdir = tmp_path / "plugins"
    pdir.mkdir()
    for name, content in files.items():
        (pdir / name).write_text(content, encoding="utf-8")
    return str(pdir)


def _plugin_src(name, body, cls_kwargs="", extra=""):
    """生成插件源码：进程级声明（message_types）+ 指定类体"""
    return (
        "from src.plugins.base import Plugin\n"
        "class P(Plugin):\n"
        "    name = %r\n" % name +
        "    triggers = []\n" +
        cls_kwargs +
        body +
        extra +
        "plugin = P()\n"
    )


PLUGIN_PROCESS_OK = _plugin_src(
    "proc_ok",
    '    def handle_message(self, ctx, msg): return {"echo": msg.get("type")}\n',
    cls_kwargs="    message_types = ['cmd_a', 'cmd_b']\n",
)

PLUGIN_PROCESS_NO_IMPL = _plugin_src(
    "proc_no_impl",
    "    pass\n",
    cls_kwargs="    message_types = ['cmd_x']\n",
)

PLUGIN_PROCESS_BAD_TYPES = _plugin_src(
    "proc_bad_types",
    '    def handle_message(self, ctx, msg): return None\n',
    cls_kwargs="    message_types = 'not-a-list'\n",
)

PLUGIN_PROCESS_BAD_IO = _plugin_src(
    "proc_bad_io",
    "    pass\n",
    cls_kwargs="    needs_io = 'yes'\n",
)


def _load_cls(tmp_path, src, fname="p.py"):
    pdir = _write_plugins(tmp_path, {fname: src})
    mod = load_module(os.path.join(pdir, fname))
    return extract_plugin_class(mod, fname)


class TestProcessPluginValidation:
    def test_valid_process_plugin(self, tmp_path):
        cls = _load_cls(tmp_path, PLUGIN_PROCESS_OK)
        assert cls is not None
        assert validate_plugin(cls)
        assert cls.message_types == ["cmd_a", "cmd_b"]

    def test_message_types_without_impl_rejected(self, tmp_path):
        cls = _load_cls(tmp_path, PLUGIN_PROCESS_NO_IMPL)
        assert cls is not None
        assert not validate_plugin(cls)

    def test_message_types_must_be_list(self, tmp_path):
        cls = _load_cls(tmp_path, PLUGIN_PROCESS_BAD_TYPES)
        assert cls is not None
        assert not validate_plugin(cls)

    def test_needs_io_must_be_bool(self, tmp_path):
        cls = _load_cls(tmp_path, PLUGIN_PROCESS_BAD_IO)
        assert cls is not None
        assert not validate_plugin(cls)


class TestProcessRegistry:
    def test_process_instance_singleton(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"a.py": PLUGIN_PROCESS_OK})
        reg = PluginRegistry([os.path.join(pdir, "a.py")])
        instances = reg.process_instances()
        assert list(instances.keys()) == ["proc_ok"]
        # 单例：两次获取同一实例
        assert instances["proc_ok"] is instances["proc_ok"]
        # 进程级插件不可会话挂载
        assert reg.instantiate("proc_ok") is None

    def test_process_instance_failure_isolated(self, tmp_path):
        bad = _plugin_src(
            "proc_boom",
            "    def handle_message(self, ctx, msg): return None\n",
            cls_kwargs="    message_types = ['cmd_x']\n",
            extra="    def __init__(self): raise RuntimeError('boom')\n",
        )
        pdir = _write_plugins(tmp_path, {
            "bad.py": bad,
            "ok.py": PLUGIN_PROCESS_OK,
        })
        reg = PluginRegistry([os.path.join(pdir, "bad.py"), os.path.join(pdir, "ok.py")])
        assert "proc_ok" in reg.process_instances()
        assert "proc_boom" not in reg.process_instances()

    def test_process_init_failure_not_registered(self, tmp_path):
        """进程级 __init__ 抛异常：不注册到 _classes，has/list_all 不报告"""
        bad = _plugin_src(
            "proc_boom2",
            "    def handle_message(self, ctx, msg): return None\n",
            cls_kwargs="    message_types = ['cmd_x']\n",
            extra="    def __init__(self): raise RuntimeError('boom')\n",
        )
        pdir = _write_plugins(tmp_path, {"bad.py": bad})
        reg = PluginRegistry([os.path.join(pdir, "bad.py")])
        assert not reg.has("proc_boom2")
        names = [item["name"] for item in reg.list_all()]
        assert "proc_boom2" not in names

    def test_process_instances_returns_copy(self, tmp_path):
        """process_instances 返回副本：修改不污染注册表内部状态"""
        pdir = _write_plugins(tmp_path, {"a.py": PLUGIN_PROCESS_OK})
        reg = PluginRegistry([os.path.join(pdir, "a.py")])
        snapshot = reg.process_instances()
        snapshot.clear()  # 修改副本
        # 内部不受影响
        assert "proc_ok" in reg.process_instances()

    def test_list_all_has_message_fields(self, tmp_path):
        pdir = _write_plugins(tmp_path, {"a.py": PLUGIN_PROCESS_OK})
        reg = PluginRegistry([os.path.join(pdir, "a.py")])
        item = reg.list_all()[0]
        assert item["messageTypes"] == ["cmd_a", "cmd_b"]
        assert item["needsIO"] is False


class _CollectConn:
    """记录 sendall 数据的假连接"""

    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, t):
        pass

    def fileno(self):
        return -1

    def close(self):
        pass


class _FakeManager:
    pass


class _Ctx:
    manager = _FakeManager()


class _RespondPlugin(Plugin):
    triggers = []
    message_types = ["cmd_a"]
    needs_io = True

    def handle_message(self, ctx, msg):
        assert ctx.io is not None  # needs_io=True → 注入 io
        return {"commandType": "cmd_a", "ok": True}


class _HandledPlugin(Plugin):
    triggers = []
    message_types = ["cmd_b"]

    def handle_message(self, ctx, msg):
        assert ctx.io is None  # needs_io=False → 无 io
        return HANDLED


class _NonePlugin(Plugin):
    triggers = []
    message_types = ["cmd_c"]

    def handle_message(self, ctx, msg):
        return None


class _BoomPlugin(Plugin):
    triggers = []
    message_types = ["cmd_d"]

    def handle_message(self, ctx, msg):
        raise RuntimeError("plugin blew up")


class TestPluginMessageHandler:
    def test_dict_response_sent(self):
        handler = PluginMessageHandler(_RespondPlugin())
        conn = _CollectConn()
        handler.handle(_Ctx(), conn, {"type": "cmd_a", "id": "s1"})
        import json
        resp = json.loads(conn.sent[-1].decode("utf-8"))
        assert resp == {"commandType": "cmd_a", "ok": True}

    def test_handled_no_response(self):
        handler = PluginMessageHandler(_HandledPlugin())
        conn = _CollectConn()
        handler.handle(_Ctx(), conn, {"type": "cmd_b"})
        assert conn.sent == []  # 插件已自行响应（多帧协议）

    def test_none_returns_error(self):
        handler = PluginMessageHandler(_NonePlugin())
        conn = _CollectConn()
        handler.handle(_Ctx(), conn, {"type": "cmd_c"})
        import json
        resp = json.loads(conn.sent[-1].decode("utf-8"))
        assert resp["type"] == "error"
        assert "未处理" in resp["message"]

    def test_exception_isolated_to_error(self):
        handler = PluginMessageHandler(_BoomPlugin())
        conn = _CollectConn()
        handler.handle(_Ctx(), conn, {"type": "cmd_d"})  # 不抛异常
        import json
        resp = json.loads(conn.sent[-1].decode("utf-8"))
        assert resp["type"] == "error"

    def test_process_context_fields(self):
        """ProcessPluginContext 携带 manager/plugin/io"""
        plugin = _RespondPlugin()
        io = object()
        pctx = ProcessPluginContext(_FakeManager(), plugin, io)
        assert pctx.manager is not None
        assert pctx.plugin is plugin
        assert pctx.io is io
