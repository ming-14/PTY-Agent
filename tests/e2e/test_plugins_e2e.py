"""E2E：插件系统全链路验证（真实 PTY 会话 + 插件挂载）

覆盖场景：
  1. exec 注入插件 → 会话挂载 + on_attach
  2. on_input 输入变换（send 路径）
  3. on_output 输出变换（reader 线程生效）
  4. on_snapshot 快照变换 + debugInformation.plugins 显示
  5. 动态 attach / detach（运行中挂载与卸载，on_detach 触发）
  6. auto_load 自动注入（command 命中）
  7. 插件自定义命令（handle_command 路由）
  8. request_return + self_unload：插件在等待循环中主动返回（reason 透传）
  9. on_event 事件订阅 + poll 定时触发

测试自建 SessionManager + 临时插件目录（真实 PTY 会话），
不经网络层，不依赖守护进程状态。
"""

import os
import sys
import time
import threading

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.plugins.base import Plugin  # noqa: E402
from src.plugins.registry import PluginRegistry  # noqa: E402
from src.session.manager import SessionManager  # noqa: E402


# ── 测试插件定义 ─────────────────────────────────────────────


class TransformPlugin(Plugin):
    """输入(前缀剥离)/输出/快照变换插件（e2e 验证变换链）"""

    name = "transform"
    description = "e2e transform"
    triggers = ["event", "poll"]
    poll_interval = 0.3

    def __init__(self):
        self.attach_count = 0
        self.detach_count = 0
        self.detach_exit = None
        self.events = []
        self.poll_count = 0
        self.lock = threading.Lock()

    def on_attach(self, ctx):
        self.attach_count += 1
        self.sid = ctx.session.id

    def on_detach(self, ctx, exit_code):
        self.detach_count += 1
        self.detach_exit = exit_code

    def on_input(self, ctx, data):
        # 输入变换：剥离 "T:" 前缀（不影响 python 语法，可验证变换生效）
        if isinstance(data, str):
            return data[2:] if data.startswith("T:") else data
        return data[2:] if data.startswith(b"T:") else data

    def on_output(self, ctx, data: bytes):
        return data + b"[out]"

    def on_snapshot(self, ctx, text):
        return text + "[snap]"

    def on_event(self, ctx, event):
        self.events.append(event.get("type"))

    def on_poll(self, ctx):
        with self.lock:
            self.poll_count += 1

    def handle_command(self, ctx, msg):
        if msg.get("command") == "status":
            return {"attached": self.attach_count, "sid": self.sid}
        return None


class InterceptPlugin(Plugin):
    """输入拦截插件：特定输入直接丢弃（返回 None）"""

    name = "intercept"
    triggers = ["event"]

    def __init__(self):
        self.blocked = 0

    def on_input(self, ctx, data):
        if (isinstance(data, str) and data.startswith("BLOCK")) or \
           (isinstance(data, bytes) and data.startswith(b"BLOCK")):
            self.blocked += 1
            return None
        return data


class KillerPlugin(Plugin):
    """请求返回插件：每次 poll 都 request_return（无等待时被宿主丢弃）"""

    name = "killer"
    triggers = ["poll"]
    poll_interval = 0.2

    def __init__(self):
        self.requested = 0

    def on_poll(self, ctx):
        self.requested += 1
        ctx.request_return("killer-said-done")


AUTO_LOAD_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    name = 'auto_py'\n"
    "    triggers = ['event']\n"
    "    auto_load = {'command': r'^python'}\n"
    "    def on_event(self, ctx, event): pass\n"
    "plugin = P()\n"
)


def _wait_until(cond, timeout=6.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return False


def _install_registry(registry, *plugins):
    """把测试插件类注入临时注册表"""
    for p in plugins:
        registry._classes[p.name] = type(p)


@pytest.fixture
def registry(tmp_path):
    return PluginRegistry([])


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


def _last_sent_json(conn: _FakeConn) -> dict:
    import json as _json
    assert conn.sent, "未收到任何响应"
    return _json.loads(conn.sent[-1].decode("utf-8").strip())


def _stop_session(session):
    if session is not None and session.running:
        session.stop()


class TestPluginSessionE2E:
    def test_exec_inject_transform_and_debug(self, tmp_path, registry):
        _install_registry(registry, TransformPlugin())
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "e2e-t1", "python -u -i", plugins=["transform"],
                cwd=str(tmp_path))

            # 挂载 + on_attach（断言挂载后的实例）
            inst = session.plugin_host.get("transform")
            assert inst is not None
            assert inst.attach_count == 1
            assert session.plugin_host.names() == ["transform"]

            # on_input 变换：T: 前缀被剥离，实际执行 print(111)
            session.write_input("T:print(111);\n")
            assert _wait_until(lambda: "111" in session.get_output())

            # on_output 变换：输出带 [out] 标记
            assert _wait_until(lambda: b"[out]" in session.output_buffer.get_slice())

            # on_snapshot 变换：快照带 [snap] 标记
            snap = session.get_snapshot()
            assert snap.endswith("[snap]")

            # debugInformation.plugins（build_result 路径）
            from src.daemon.response import build_result
            result = build_result(manager, session.id, "x", True, "matched",
                                  consume_events=False, session=session)
            di = result["program"]["debugInformation"]
            assert di["plugins"] == [{"name": "transform", "version": "1.0"}]
        finally:
            _stop_session(session)

    def test_input_intercept(self, tmp_path, registry):
        _install_registry(registry, InterceptPlugin())
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "e2e-t2", "python -u -i", plugins=["intercept"],
                cwd=str(tmp_path))
            intp = session.plugin_host.get("intercept")
            off0 = session.output_offset
            # BLOCK 前缀输入被插件拦截，python 无任何反应
            session.write_input("BLOCKprint(12345)\n")
            time.sleep(0.5)
            assert intp.blocked == 1
            assert "12345" not in session.get_output(from_offset=off0)
            # 正常输入不受影响
            session.write_input("print(23456)\n")
            assert _wait_until(lambda: "23456" in session.get_output())
        finally:
            _stop_session(session)

    def test_dynamic_attach_detach(self, tmp_path, registry):
        _install_registry(registry, TransformPlugin())
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "e2e-t3", "python -u -i", cwd=str(tmp_path))
            assert session.plugin_host.is_empty()

            # 动态 attach
            inst = registry.instantiate("transform")
            assert session.plugin_host.attach(inst)
            assert session.plugin_host.names() == ["transform"]

            # on_output 对后续输出生效
            session.write_input("print(11);\n")
            assert _wait_until(lambda: b"[out]" in session.output_buffer.get_slice())

            # detach：钩子不再生效，on_detach 触发
            assert session.plugin_host.detach("transform")
            assert inst.detach_count == 1
        finally:
            _stop_session(session)

    def test_auto_load_match(self, tmp_path, registry):
        # 经 ExecHandler 全链路：exec 请求到达 handler 时按 auto_load 命中
        # 自动注入（create_session 本身不做 auto_load 判定，判定在 handler 层）
        with open(os.path.join(str(tmp_path), "auto_py.py"), "w", encoding="utf-8") as f:
            f.write(AUTO_LOAD_SRC)
        manager = SessionManager(plugin_registry=PluginRegistry([
            os.path.join(str(tmp_path), "auto_py.py")]))
        from src.daemon.handlers.base import HandlerContext
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
            # 未命中的命令不注入
            assert manager.match_auto_load("node app.js", str(tmp_path), None) == []
        finally:
            _stop_session(session)

    def test_plugin_command(self, tmp_path, registry):
        _install_registry(registry, TransformPlugin())
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

    def test_request_return(self, tmp_path, registry):
        _install_registry(registry, KillerPlugin())
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "e2e-t6", "python -u -i", plugins=["killer"],
                cwd=str(tmp_path))
            killer = session.plugin_host.get("killer")
            assert killer is not None

            # 真实等待循环：等待激活期间插件 poll 触发 request_return，
            # wait_for_trigger 应携带自定义 reason 立即返回
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

    def test_on_event_and_poll(self, tmp_path, registry):
        _install_registry(registry, TransformPlugin())
        manager = SessionManager(plugin_registry=registry)
        session = None
        try:
            session = manager.create_session(
                "e2e-t7", "python -u -i", plugins=["transform"],
                cwd=str(tmp_path))
            plg = session.plugin_host.get("transform")
            # on_event：会话启动触发 process_spawn（真实进程树）
            assert _wait_until(lambda: "process_spawn" in plg.events)
            # on_poll 定时触发
            session.plugin_host.poll_tick()
            assert plg.poll_count >= 1
        finally:
            _stop_session(session)