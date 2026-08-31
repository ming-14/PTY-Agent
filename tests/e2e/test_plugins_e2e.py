"""E2E：插件系统全链路验证（真实 PTY 会话 + 插件挂载）

覆盖场景：
  1. exec 注入插件 → 会话挂载 + on_init/on_attach
  2. on_input 输入变换（send 路径）
  3. on_output 输出变换（reader 线程生效）
  4. on_snapshot 快照变换 + debugInformation.plugins 显示
  5. 动态 attach / detach（运行中挂载与卸载，on_detach 触发）
  6. auto_load 自动注入（command 命中）
  7. 插件自定义命令（handle_command 路由）
  8. request_return + self_unload：插件在等待循环中主动返回（reason 透传）
  9. on_event 事件订阅 + poll 定时触发
  10. 事件总线：session.created / session.event.<类型> 发布

测试自建 SessionManager + 临时插件目录（真实 PTY 会话），
不经网络层，不依赖守护进程状态。
"""

import os
import sys
import time
import threading

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.plugins.registry import PluginRegistry  # noqa: E402
from src.session.manager import SessionManager  # noqa: E402
from tests.helpers import write_plugin_dir  # noqa: E402


# ── 测试插件定义（临时目录 + 清单 + 源码） ────────────────

TRANSFORM_SRC = (
    "from src.plugins.base import Plugin\n"
    "import threading\n"
    "class TransformPlugin(Plugin):\n"
    "    def __init__(self):\n"
    "        self.attach_count = 0\n"
    "        self.detach_count = 0\n"
    "        self.detach_exit = None\n"
    "        self.events = []\n"
    "        self.poll_count = 0\n"
    "        self.lock = threading.Lock()\n"
    "    def on_init(self, ctx):\n"
    "        self.inited = True\n"
    "    def on_attach(self, ctx):\n"
    "        self.attach_count += 1\n"
    "        self.sid = ctx.session.id\n"
    "    def on_detach(self, ctx, exit_code):\n"
    "        self.detach_count += 1\n"
    "        self.detach_exit = exit_code\n"
    "    def on_input(self, ctx, data):\n"
    "        if isinstance(data, str):\n"
    "            return data[2:] if data.startswith('T:') else data\n"
    "        return data[2:] if data.startswith(b'T:') else data\n"
    "    def on_output(self, ctx, data):\n"
    "        return data + b'[out]'\n"
    "    def on_snapshot(self, ctx, text):\n"
    "        return text + '[snap]'\n"
    "    def on_event(self, ctx, event):\n"
    "        self.events.append(event.get('type'))\n"
    "    def on_poll(self, ctx):\n"
    "        with self.lock:\n"
    "            self.poll_count += 1\n"
    "    def handle_command(self, ctx, msg):\n"
    "        if msg.get('command') == 'status':\n"
    "            return {'attached': self.attach_count, 'sid': self.sid}\n"
    "        return None\n"
    "plugin = TransformPlugin()\n"
)

INTERCEPT_SRC = (
    "from src.plugins.base import Plugin\n"
    "class InterceptPlugin(Plugin):\n"
    "    def __init__(self):\n"
    "        self.blocked = 0\n"
    "    def on_input(self, ctx, data):\n"
    "        if (isinstance(data, str) and data.startswith('BLOCK')) or \\\n"
    "           (isinstance(data, bytes) and data.startswith(b'BLOCK')):\n"
    "            self.blocked += 1\n"
    "            return None\n"
    "        return data\n"
    "plugin = InterceptPlugin()\n"
)

KILLER_SRC = (
    "from src.plugins.base import Plugin\n"
    "class KillerPlugin(Plugin):\n"
    "    def __init__(self):\n"
    "        self.requested = 0\n"
    "    def on_poll(self, ctx):\n"
    "        self.requested += 1\n"
    "        ctx.request_return('killer-said-done')\n"
    "plugin = KillerPlugin()\n"
)

AUTO_LOAD_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def on_event(self, ctx, event): pass\n"
    "plugin = P()\n"
)

BUS_EVENT_SRC = (
    "from src.plugins.base import Plugin\n"
    "class BusPlugin(Plugin):\n"
    "    def __init__(self):\n"
    "        self.events = []\n"
    "    def on_bus_event(self, ctx, event):\n"
    "        self.events.append((event.topic, event.payload.get('sessionId')))\n"
    "plugin = BusPlugin()\n"
)


def _wait_until(cond, timeout=6.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return False


def _stop_session(session):
    if session is not None and session.running:
        session.stop()


class TestPluginSessionE2E:
    def test_exec_inject_transform_and_debug(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "transform", "session", TRANSFORM_SRC,
            manifest_extra={
                "triggers": ["event", "poll"], "pollInterval": 0.3,
            },
        )
        registry = PluginRegistry([pdir])
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "e2e-t1", "python -u -i", plugins=["transform"],
                cwd=str(tmp_path))

            inst = session.plugin_host.get("transform")
            assert inst is not None
            assert inst.inited is True  # on_init 已回调
            assert inst.attach_count == 1
            assert session.plugin_host.names() == ["transform"]

            # on_input 变换：T: 前缀被剥离
            session.write_input("T:print(111);\n")
            assert _wait_until(lambda: "111" in session.get_output())

            # on_output 变换
            assert _wait_until(lambda: b"[out]" in session.output_buffer.get_slice())

            # on_snapshot 变换
            snap = session.get_snapshot()
            assert snap.endswith("[snap]")

            # debugInformation.plugins
            from src.execution.response import build_result
            result = build_result(manager, session.id, "x", True, "matched",
                                  consume_events=False, session=session)
            di = result["program"]["debugInformation"]
            assert di["plugins"] == [{"name": "transform", "version": "1.0", "options": {}}]
        finally:
            _stop_session(session)

    def test_input_intercept(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "intercept", "session", INTERCEPT_SRC)
        registry = PluginRegistry([pdir])
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "e2e-t2", "python -u -i", plugins=["intercept"],
                cwd=str(tmp_path))
            intp = session.plugin_host.get("intercept")
            off0 = session.output_offset
            session.write_input("BLOCKprint(12345)\n")
            time.sleep(0.5)
            assert intp.blocked == 1
            assert "12345" not in session.get_output(from_offset=off0)
            session.write_input("print(23456)\n")
            assert _wait_until(lambda: "23456" in session.get_output())
        finally:
            _stop_session(session)

    def test_dynamic_attach_detach(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "transform", "session", TRANSFORM_SRC,
            manifest_extra={"triggers": ["event", "poll"], "pollInterval": 0.3},
        )
        registry = PluginRegistry([pdir])
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "e2e-t3", "python -u -i", cwd=str(tmp_path))
            assert session.plugin_host.is_empty()

            inst = registry.instantiate("transform")
            assert session.plugin_host.attach(inst)
            assert session.plugin_host.names() == ["transform"]

            session.write_input("print(11);\n")
            assert _wait_until(lambda: b"[out]" in session.output_buffer.get_slice())

            assert session.plugin_host.detach("transform")
            assert inst.detach_count == 1
        finally:
            _stop_session(session)

    def test_auto_load_match(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "auto_py", "session", AUTO_LOAD_SRC,
            manifest_extra={"autoLoad": {"command": r"^python"}},
        )
        registry = PluginRegistry([pdir])
        manager = SessionManager(plugin_registry=registry)
        from src.execution.context import HandlerContext
        from src.daemon.handlers.exec_handler import ExecHandler

        conn = _FakeConn()
        ExecHandler().handle(
            HandlerContext(manager, None, None), conn,
            {"type": "exec", "id": "e2e-t4", "command": "python -u -i",
             "cwd": str(tmp_path), "timeout": 1, "explicit_timeout": True,
             "full": True},
        )
        session = manager.get_session("e2e-t4")
        try:
            assert session is not None
            assert "auto_py" in session.plugin_host.names()
            assert manager.match_auto_load("node app.js", str(tmp_path), None) == []
        finally:
            _stop_session(session)

    def test_plugin_command(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "transform", "session", TRANSFORM_SRC,
            manifest_extra={"triggers": ["event", "poll"], "pollInterval": 0.3},
        )
        registry = PluginRegistry([pdir])
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "e2e-t5", "python -u -i", plugins=["transform"],
                cwd=str(tmp_path))
            assert session.plugin_host.handle_command(
                "transform", {"command": "status"}) == {"attached": 1, "sid": "e2e-t5"}
            assert session.plugin_host.handle_command(
                "transform", {"command": "nope"}) is None
            assert session.plugin_host.handle_command("ghost", {}) is None
        finally:
            _stop_session(session)

    def test_request_return(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "killer", "session", KILLER_SRC,
            manifest_extra={"triggers": ["poll"], "pollInterval": 0.2},
        )
        registry = PluginRegistry([pdir])
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "e2e-t6", "python -u -i", plugins=["killer"],
                cwd=str(tmp_path))
            killer = session.plugin_host.get("killer")
            assert killer is not None

            def _future_poll():
                time.sleep(0.5)
                session.plugin_host.poll_tick()

            t = threading.Thread(target=_future_poll, daemon=True)
            t.start()
            matched, reason = session.wait_for_trigger(timeout=8)
            assert matched is True
            assert reason == "killer-said-done"
            assert killer.requested >= 1
        finally:
            _stop_session(session)

    def test_on_event_and_poll(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "transform", "session", TRANSFORM_SRC,
            manifest_extra={"triggers": ["event", "poll"], "pollInterval": 0.3},
        )
        registry = PluginRegistry([pdir])
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "e2e-t7", "python -u -i", plugins=["transform"],
                cwd=str(tmp_path))
            plg = session.plugin_host.get("transform")
            assert _wait_until(lambda: "process_spawn" in plg.events)
            session.plugin_host.poll_tick()
            assert plg.poll_count >= 1
        finally:
            _stop_session(session)

    def test_event_bus_publishes_session_events(self, tmp_path):
        """daemon 事件总线：session.created 与 session.event.<类型> 发布到订阅插件"""
        pdir = write_plugin_dir(
            tmp_path, "bus", "session", BUS_EVENT_SRC,
            manifest_extra={"events": {"subscribe": ["session.created", "session.event.>"]}},
        )
        registry = PluginRegistry([pdir])
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "e2e-t8", "python -u -i", cwd=str(tmp_path))
            # 规范实例收到 session.created
            canonical = registry._entries["bus"].instance
            assert _wait_until(lambda: any(
                t == "session.created" and sid == "e2e-t8"
                for t, sid in canonical.events
            ))
            # 会话运行中产生 process_spawn 事件（session.event.process_spawn）
            assert _wait_until(lambda: any(
                t == "session.event.process_spawn"
                for t, _ in canonical.events
            ))
        finally:
            _stop_session(session)

    

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
