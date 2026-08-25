"""插件宿主（PluginHost）单测：钩子链、触发调度、返回控制、自我卸载"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.base import Plugin
from src.plugins.host import PluginHost
from tests.helpers import make_manifest, attach_manifest


class FakeSession:
    def __init__(self):
        self.id = "test-session"
        self.running = True


class LoggingPlugin(Plugin):
    version = "1.0"

    def __init__(self, name="lp"):
        self.name = name
        self.calls = []
        self.attached = False
        self.detached = []

    def on_attach(self, ctx):
        self.calls.append(("attach", ctx.session.id))
        self.attached = True
        self.ctx = ctx

    def on_detach(self, ctx, exit_code):
        self.calls.append(("detach", exit_code))
        self.detached.append(exit_code)

    def on_input(self, ctx, data):
        self.calls.append(("input", data))
        return data

    def on_output(self, ctx, data):
        self.calls.append(("output", data))
        return data

    def on_snapshot(self, ctx, text):
        self.calls.append(("snapshot", text))
        return text

    def handle_command(self, ctx, msg):
        self.calls.append(("cmd", msg))
        return {"echo": msg}


class EventPlugin(Plugin):
    def __init__(self, name="ep"):
        self.name = name
        self.events = []

    def on_event(self, ctx, event):
        self.events.append(event)


class PollPlugin(Plugin):
    def __init__(self, name="pp"):
        self.name = name
        self.count = 0
        self.lock = threading.Lock()

    def on_poll(self, ctx):
        with self.lock:
            self.count += 1


class InterceptPlugin(Plugin):
    def __init__(self, name="intercept"):
        self.name = name

    def on_input(self, ctx, data):
        return None


class TransformerPlugin(Plugin):
    def __init__(self, name="transformer"):
        self.name = name

    def on_input(self, ctx, data):
        return data + "X"

    def on_output(self, ctx, data):
        return data.replace(b"a", b"b")

    def on_snapshot(self, ctx, text):
        return text + "[tag]"


class CrashPlugin(Plugin):
    def __init__(self, name="crash"):
        self.name = name

    def on_output(self, ctx, data):
        raise RuntimeError("boom")


def _attach(host, *plugins):
    """挂载插件并设置最小清单（引擎注册所需）"""
    for p in plugins:
        triggers = []
        if hasattr(p, "on_event") and p.on_event is not Plugin.on_event:
            triggers.append("event")
        if hasattr(p, "on_poll") and p.on_poll is not Plugin.on_poll:
            triggers.append("poll")
        p.manifest = make_manifest(p.name, triggers=triggers, poll_interval=0.2)
        host.attach(p)


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def host(session):
    return PluginHost(session, plugins=[])


class TestLifecycle:
    def test_attach_calls_on_attach_with_ctx(self, session):
        p = LoggingPlugin()
        h = PluginHost(session, plugins=[p])
        assert p.attached
        assert p.calls[0] == ("attach", "test-session")
        assert p.ctx.session is session
        assert p.ctx.plugin is p

    def test_duplicate_name_rejected(self, session):
        p1, p2 = LoggingPlugin("dup"), LoggingPlugin("dup")
        h = PluginHost(session, plugins=[p1])
        assert not h.attach(p2)
        assert h.names() == ["dup"]

    def test_detach_calls_on_detach(self, session):
        p = LoggingPlugin()
        h = PluginHost(session, plugins=[p])
        assert h.detach("lp", exit_code=1)
        assert p.detached == [1]
        assert not h.detach("lp")

    def test_detach_all_power_idempotent(self, session):
        p1, p2 = LoggingPlugin("a"), LoggingPlugin("b")
        h = PluginHost(session, plugins=[p1, p2])
        h.detach_all(exit_code=0)
        assert sorted(p1.detached + p2.detached) == [0, 0]
        h.detach_all()
        assert len(p1.detached) == 1

    def test_snapshot_info(self, session):
        h = PluginHost(session, plugins=[LoggingPlugin("a")])
        info = h.snapshot_info()
        assert info == [{"name": "a", "version": "1.0"}]
        assert PluginHost(session, plugins=[]).snapshot_info() is None


class TestHookChains:
    def test_input_chain_order(self, session):
        p1 = TransformerPlugin("t1")
        p2 = TransformerPlugin("t2")
        h = PluginHost(session, plugins=[p1, p2])
        assert h.on_input("in") == "inXX"

    def test_input_intercept_none(self, session):
        t = TransformerPlugin("t1")
        t2 = TransformerPlugin("t2")
        intercept = InterceptPlugin()
        h = PluginHost(session, plugins=[t, intercept, t2])
        assert h.on_input("in") is None

    def test_output_chain_bytes(self, session):
        p = TransformerPlugin()
        h = PluginHost(session, plugins=[p])
        assert h.on_output(b"alpha") == b"blphb"

    def test_snapshot_chain(self, session):
        p = TransformerPlugin()
        h = PluginHost(session, plugins=[p])
        assert h.on_snapshot("text") == "text[tag]"

    def test_exception_isolated_and_chain_continues(self, session):
        crash = CrashPlugin()
        transformer = TransformerPlugin()
        h = PluginHost(session, plugins=[crash, transformer])
        assert h.on_output(b"a") == b"b"
        assert h.names() == ["crash", "transformer"]


class TestTriggers:
    def test_event_only_to_declared_plugins(self, session):
        ep = EventPlugin()
        lp = LoggingPlugin()
        h = PluginHost(session, plugins=[ep])
        h.on_event({"type": "process_crash"})
        assert ep.events == [{"type": "process_crash"}]

    def test_poll_throttled_by_interval(self, session):
        p = PollPlugin()
        attach_manifest(p, make_manifest("pp", triggers=["poll"], poll_interval=0.2))
        h = PluginHost(session, plugins=[p])
        time.sleep(0.05)
        h.poll_tick()
        assert p.count == 1
        time.sleep(0.05)
        h.poll_tick()
        assert p.count == 1
        time.sleep(0.25)
        h.poll_tick()
        assert p.count == 2

    def test_poll_stops_after_detach(self, session):
        p = PollPlugin()
        h = PluginHost(session, plugins=[p])
        h.detach("pp")
        h.poll_tick()
        assert p.count == 0


class TestReturnControl:
    def test_request_return_only_when_waiting(self, session):
        p = LoggingPlugin()
        h = PluginHost(session, plugins=[p])
        assert h.request_return("x") is False
        h.enter_wait()
        assert h.request_return("why") is True
        assert h.consume_return_request() == "why"
        assert h.consume_return_request() is None
        h.exit_wait()
        assert h.request_return("y") is False

    def test_exit_wait_clears_pending(self, session):
        h = PluginHost(session, plugins=[])
        h.enter_wait()
        h.request_return("z")
        h.exit_wait()
        assert h.consume_return_request() is None

    def test_ctx_request_return(self, session):
        p = LoggingPlugin()
        h = PluginHost(session, plugins=[p])
        h.enter_wait()
        ctx = p.ctx
        assert ctx.request_return("ctx-reason") is True
        h.exit_wait()


class TestSelfUnload:
    def test_self_unload_after_chain(self, session):
        unloaded = []

        class SelfUnloadPlugin(Plugin):
            def __init__(self):
                self.name = "su"
                self.saw = []

            def on_snapshot(self, ctx, text):
                self.saw.append(text)
                if text == "kill":
                    ctx.self_unload()
                return text

            def on_detach(self, ctx, exit_code):
                unloaded.append(exit_code)

        p = SelfUnloadPlugin()
        h = PluginHost(session, plugins=[p])
        assert h.on_snapshot("text") == "text"
        assert h.names() == ["su"]
        assert h.on_snapshot("kill") == "kill"
        assert h.names() == []
        assert unloaded == [None]
        assert h.on_snapshot("kill") == "kill"

    def test_self_unload_during_output_chain(self, session):
        seen = []

        class Mid(Plugin):
            name = "mid"
            def on_output(self, ctx, data):
                seen.append("mid")
                return data

        class Su(Plugin):
            name = "su"
            def on_output(self, ctx, data):
                seen.append("su")
                ctx.self_unload()
                return data

        p_mid, p_su = Mid(), Su()
        h = PluginHost(session, plugins=[p_mid, p_su])
        h.on_output(b"x")
        assert seen == ["mid", "su"]
        assert h.names() == ["mid"]

    def test_self_unload_only_once(self, session):
        class Once(Plugin):
            name = "once"
            def on_input(self, ctx, data):
                ctx.self_unload()
                return data

        p = Once()
        h = PluginHost(session, plugins=[p])
        h.on_input(b"a")
        h.on_input(b"b")
        assert h.names() == []


class TestInspectState:
    def test_provide_first_non_none(self, session):
        class A(Plugin):
            name = "a"
            def inspect_state(self, ctx): return None

        class B(Plugin):
            name = "b"
            def inspect_state(self, ctx): return {"from": "b"}

        class C(Plugin):
            name = "c"
            def inspect_state(self, ctx): return {"from": "c"}

        h = PluginHost(session, plugins=[A(), B(), C()])
        assert h.inspect_state() == {"from": "b"}

    def test_all_none(self, session):
        class A(Plugin):
            name = "a"
            def inspect_state(self, ctx): return None

        h = PluginHost(session, plugins=[A()])
        assert h.inspect_state() is None

    def test_empty_chain(self, session):
        h = PluginHost(session, plugins=[])
        assert h.inspect_state() is None


class TestHandleCommand:
    def test_route_to_plugin(self, session):
        p = LoggingPlugin()
        h = PluginHost(session, plugins=[p])
        assert h.handle_command("lp", {"command": "status"}) == {"echo": {"command": "status"}}

    def test_unknown_plugin_or_unhandled(self, session):
        p = LoggingPlugin()
        h = PluginHost(session, plugins=[p])
        assert h.handle_command("ghost", {}) is None


