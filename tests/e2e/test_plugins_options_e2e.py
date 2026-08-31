"""E2E：插件自定义 CLI 选项（cliOptions）全链路验证（真实 PTY 会话）

覆盖场景：
  1. exec 携带 pluginOptions → 会话插件挂载，on_attach/钩子经 ctx.options 读取
  2. send 携带 pluginOptions → 会话插件选项合并更新，后续钩子读到新值
  3. exec 附加已存在会话 → 选项合并更新
  4. exec/send 非法 pluginOptions → 请求被拒绝（error 响应）
  5. read 携带 pluginOptions → 选项合并更新

测试自建 SessionManager + 临时插件目录（真实 PTY 会话），
不经网络层，不依赖守护进程状态。
"""

import json
import os
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.plugins.registry import PluginRegistry  # noqa: E402
from src.session.manager import SessionManager  # noqa: E402
from src.execution.context import HandlerContext  # noqa: E402
from src.daemon.handlers.exec_handler import ExecHandler  # noqa: E402
from src.daemon.handlers.send_handler import SendHandler  # noqa: E402
from src.daemon.handlers.read_handler import ReadHandler  # noqa: E402
from tests.helpers import write_plugin_dir  # noqa: E402


OPT_PLUGIN_SRC = (
    "from src.plugins.base import Plugin\n"
    "class OptPlugin(Plugin):\n"
    "    def __init__(self):\n"
    "        self.seen = []\n"
    "    def on_attach(self, ctx):\n"
    "        self.seen.append(('attach', dict(ctx.options)))\n"
    "    def on_input(self, ctx, data):\n"
    "        self.seen.append(('input', dict(ctx.options)))\n"
    "        return data\n"
    "    def on_snapshot(self, ctx, text):\n"
    "        self.seen.append(('snapshot', dict(ctx.options)))\n"
    "        return text\n"
    "    def handle_command(self, ctx, msg):\n"
    "        if msg.get('command') == 'options':\n"
    "            return dict(ctx.options)\n"
    "        return None\n"
    "plugin = OptPlugin()\n"
)

OPTIONS = [
    {"name": "pa", "short": "p", "type": "str", "default": None, "help": "param"},
    {"name": "num", "type": "int", "default": None, "help": "num"},
]


def _wait_until(cond, timeout=6.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return False


class _FakeConn:
    """捕获 Message.send 输出的假连接"""

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


def _decode_responses(conn):
    """从假连接字节流解码响应消息列表"""
    from src.protocol.envelope import unwrap
    out = []
    for chunk in conn.sent:
        try:
            _, body, _ = unwrap(json.loads(chunk.decode("utf-8")))
            out.append(body)
        except Exception:
            pass
    return out


class TestPluginOptionsE2E:
    def _registry(self, tmp_path, with_conflict=False):
        pdir = write_plugin_dir(
            tmp_path, "opt", "session", OPT_PLUGIN_SRC,
            manifest_extra={"cliOptions": OPTIONS,
                            "hooks": {"handle_command": {}}},
        )
        dirs = [pdir]
        if with_conflict:
            bad = write_plugin_dir(
                tmp_path, "bad", "session", OPT_PLUGIN_SRC,
                manifest_extra={"cliOptions": [
                    {"name": "timeout", "commands": ["exec"]}]},
            )
            dirs.append(bad)
        return PluginRegistry(dirs)

    def test_exec_injects_options_readable_in_hooks(self, tmp_path):
        registry = self._registry(tmp_path)
        manager = SessionManager(plugin_registry=registry)
        conn = _FakeConn()
        ExecHandler().handle(
            HandlerContext(manager, None, None), conn,
            {"type": "exec", "id": "opt-e1", "command": "python -u -i",
             "cwd": str(tmp_path), "timeout": 1, "explicit_timeout": True,
             "full": True, "plugins": ["opt"],
             "pluginOptions": {"opt": {"pa": "hello", "num": 5}}},
        )
        session = manager.get_session("opt-e1")
        try:
            assert session is not None
            inst = session.plugin_host.get("opt")
            assert inst is not None
            # on_attach 读到选项
            assert ("attach", {"pa": "hello", "num": 5}) in inst.seen
            # 选项进入输入钩子（写入触发 on_input）
            session.write_input("print(1)\n")
            assert _wait_until(lambda: any(
                k == "input" and v == {"pa": "hello", "num": 5} for k, v in inst.seen
            )), inst.seen
            # 快照钩子同样携带选项
            session.get_snapshot()
            assert any(k == "snapshot" and v == {"pa": "hello", "num": 5}
                       for k, v in inst.seen), inst.seen
            # handle_command 经 ctx.options 可读
            assert session.plugin_host.handle_command(
                "opt", {"command": "options"}) == {"pa": "hello", "num": 5}
            # 快照信息包含选项
            assert session.plugin_host.snapshot_info() == [
                {"name": "opt", "version": "1.0", "options": {"pa": "hello", "num": 5}},
            ]
        finally:
            _stop(session)

    def test_send_updates_session_options(self, tmp_path):
        registry = self._registry(tmp_path)
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "opt-e2", "python -u -i", plugins=["opt"], cwd=str(tmp_path))
            inst = session.plugin_host.get("opt")
            assert ("attach", {}) in inst.seen  # 未带选项挂载

            # send 携带 pluginOptions → 合并更新
            conn = _FakeConn()
            SendHandler().handle(
                HandlerContext(manager, None, None), conn,
                {"type": "send", "id": "opt-e2", "input": "print(2)\n",
                 "timeout": 1, "explicit_timeout": True,
                 "pluginOptions": {"opt": {"pa": "updated"}}},
            )
            assert session.plugin_host.options_for("opt") == {"pa": "updated"}
            assert _wait_until(lambda: any(
                k == "input" and v == {"pa": "updated"} for k, v in inst.seen
            )), inst.seen

            # read 再合并
            conn2 = _FakeConn()
            ReadHandler().handle(
                HandlerContext(manager, None, None), conn2,
                {"type": "read", "id": "opt-e2", "timeout": 1,
                 "explicit_timeout": True,
                 "pluginOptions": {"opt": {"num": 9}}},
            )
            assert session.plugin_host.options_for("opt") == {"pa": "updated", "num": 9}
        finally:
            _stop(session)

    def test_exec_on_existing_session_updates_options(self, tmp_path):
        registry = self._registry(tmp_path)
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "opt-e3", "python -u -i", plugins=["opt"], cwd=str(tmp_path))
            conn = _FakeConn()
            ExecHandler().handle(
                HandlerContext(manager, None, None), conn,
                {"type": "exec", "id": "opt-e3", "command": "python -u -i",
                 "cwd": str(tmp_path), "timeout": 1, "explicit_timeout": True,
                 "full": True,
                 "pluginOptions": {"opt": {"pa": "attached"}}},
            )
            assert session.plugin_host.options_for("opt") == {"pa": "attached"}
        finally:
            _stop(session)

    def test_invalid_plugin_options_rejected(self, tmp_path):
        registry = self._registry(tmp_path)
        manager = SessionManager(plugin_registry=registry)

        conn = _FakeConn()
        ExecHandler().handle(
            HandlerContext(manager, None, None), conn,
            {"type": "exec", "id": "opt-bad", "command": "python -u -i",
             "cwd": str(tmp_path), "timeout": 1, "explicit_timeout": True,
             "full": True, "pluginOptions": {"opt": "not-a-dict"}},
        )
        assert manager.get_session("opt-bad") is None
        bodies = _decode_responses(conn)
        assert any("pluginOptions" in str(b.get("message", "")) for b in bodies)

        conn2 = _FakeConn()
        session = manager.create_session(
            "opt-bad2", "python -u -i", plugins=["opt"], cwd=str(tmp_path))
        try:
            SendHandler().handle(
                HandlerContext(manager, None, None), conn2,
                {"type": "send", "id": "opt-bad2", "input": "print(3)\n",
                 "timeout": 1, "explicit_timeout": True,
                 "pluginOptions": {"opt": {"pa": ["list-not-scalar"]}}},
            )
            assert session.plugin_host.options_for("opt") == {}
        finally:
            _stop(session)

    def test_conflicted_plugin_not_loaded(self, tmp_path):
        registry = self._registry(tmp_path, with_conflict=True)
        assert registry.info("bad")["state"] == "broken"
        assert "CLI 选项冲突" in registry.info("bad")["error"]
        # 冲突插件不可挂载
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "opt-e5", "python -u -i", plugins=["bad"], cwd=str(tmp_path))
            assert session.plugin_host.is_empty()
        finally:
            _stop(session)


def _stop(session):
    if session is not None and session.running:
        session.stop()
