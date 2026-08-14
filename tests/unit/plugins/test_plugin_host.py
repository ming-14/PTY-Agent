"""插件宿主（PluginHost）单测：钩子链、触发调度、返回控制、自我卸载"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.base import Plugin
from src.plugins.host import PluginHost


class FakeSession:
    def __init__(self):
        self.id = "test-session"
        self.running = True


class LoggingPlugin(Plugin):
    """记录所有钩子调用与参数，供断言"""

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
        self.triggers = ["event"]
        self.events = []

    def on_event(self, ctx, event):
        self.events.append(event)


class PollPlugin(Plugin):
    def __init__(self, name="pp"):
        self.name = name
        self.triggers = ["poll"]
        self.poll_interval = 0.2
        self.count = 0
        self.lock = threading.Lock()

    def on_poll(self, ctx):
        with self.lock:
            self.count += 1


class InterceptPlugin(Plugin):
    name = "intercept"

    def on_input(self, ctx, data):
        return None


class TransformerPlugin(Plugin):
    name = "transformer"

    def on_input(self, ctx, data):
        return data + "X"

    def on_output(self, ctx, data):
        return data.replace(b"a", b"b")

    def on_snapshot(self, ctx, text):
        return text + "[tag]"


class CrashPlugin(Plugin):
    name = "crash"

    def on_output(self, ctx, data):
        raise RuntimeError("boom")


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def host(session):
    return PluginHost(session, [])


class TestLifecycle:
    def test_attach_calls_on_attach_with_ctx(self, session):
        p = LoggingPlugin()
        h = PluginHost(session, [p])
        assert p.attached
        assert p.calls[0] == ("attach", "test-session")
        assert p.ctx.session is session
        assert p.ctx.plugin is p

    def test_duplicate_name_rejected(self, session):
        p1, p2 = LoggingPlugin("dup"), LoggingPlugin("dup")
        h = PluginHost(session, [p1])
        assert not h.attach(p2)
        assert h.names() == ["dup"]

    def test_detach_calls_on_detach(self, session):
        p = LoggingPlugin()
        h = PluginHost(session, [p])
        assert h.detach("lp", exit_code=1)
        assert p.detached == [1]
        assert not h.detach("lp")

    def test_detach_all_power_idempotent(self, session):
        p1, p2 = LoggingPlugin("a"), LoggingPlugin("b")
        h = PluginHost(session, [p1, p2])
        h.detach_all(exit_code=0)
        assert sorted(p1.detached + p2.detached) == [0, 0]
        h.detach_all()
        assert len(p1.detached) == 1

    def test_snapshot_info(self, session):
        h = PluginHost(session, [LoggingPlugin("a")])
        info = h.snapshot_info()
        assert info == [{"name": "a", "version": "1.0"}]
        assert PluginHost(session, []).snapshot_info() is None


class TestHookChains:
    def test_input_chain_order(self, session):
        p1 = TransformerPlugin()
        p2 = TransformerPlugin()
        p2.name = "transformer2"
        h = PluginHost(session, [p1, p2])
        assert h.on_input("in") == "inXX"

    def test_input_intercept_none(self, session):
        t = TransformerPlugin()
        t2 = TransformerPlugin()
        t2.name = "transformer2"
        h = PluginHost(session, [t, InterceptPlugin(), t2])
        assert h.on_input("in") is None

    def test_output_chain_bytes(self, session):
        p = TransformerPlugin()
        h = PluginHost(session, [p])
        assert h.on_output(b"alpha") == b"blphb"

    def test_snapshot_chain(self, session):
        p = TransformerPlugin()
        h = PluginHost(session, [p])
        assert h.on_snapshot("text") == "text[tag]"

    def test_exception_isolated_and_chain_continues(self, session):
        crash = CrashPlugin()
        transformer = TransformerPlugin()
        h = PluginHost(session, [crash, transformer])
        # 异常插件不阻断后续插件
        assert h.on_output(b"a") == b"b"
        assert h.names() == ["crash", "transformer"]


class TestTriggers:
    def test_event_only_to_declared_plugins(self, session):
        ep = EventPlugin()
        lp = LoggingPlugin()  # triggers 默认 ["event"] 但未实现 on_event → 无影响
        h = PluginHost(session, [ep])
        h.on_event({"type": "process_crash"})
        assert ep.events == [{"type": "process_crash"}]

    def test_poll_throttled_by_interval(self, session):
        p = PollPlugin()
        h = PluginHost(session, [p])
        time.sleep(0.05)
        h.poll_tick()
        assert p.count == 1
        time.sleep(0.05)
        h.poll_tick()
        assert p.count == 1  # 未到间隔不触发
        time.sleep(0.25)
        h.poll_tick()
        assert p.count == 2

    def test_poll_stops_after_detach(self, session):
        p = PollPlugin()
        h = PluginHost(session, [p])
        h.detach("pp")
        h.poll_tick()
        assert p.count == 0


class TestReturnControl:
    def test_request_return_only_when_waiting(self, session):
        p = LoggingPlugin()
        h = PluginHost(session, [p])
        assert h.request_return("x") is False  # 无等待 → 丢弃
        h.enter_wait()
        assert h.request_return("why") is True
        assert h.consume_return_request() == "why"
        assert h.consume_return_request() is None  # 一次消费
        h.exit_wait()
        assert h.request_return("y") is False

    def test_exit_wait_clears_pending(self, session):
        h = PluginHost(session, [])
        h.enter_wait()
        h.request_return("z")
        h.exit_wait()
        assert h.consume_return_request() is None

    def test_ctx_request_return(self, session):
        p = LoggingPlugin()
        h = PluginHost(session, [p])
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
        h = PluginHost(session, [p])
        assert h.on_snapshot("text") == "text"
        assert h.names() == ["su"]          # 未触发卸载请求
        assert h.on_snapshot("kill") == "kill"
        assert h.names() == []               # 链结束后已卸载
        assert unloaded == [None]
        # 已卸载，后续钩子不再调用
        assert h.on_snapshot("kill") == "kill"

    def test_self_unload_during_output_chain(self, session):
        seen = []

        class Mid(Plugin):
            name = "mid"

            def on_output(self, ctx, data):
                seen.append(("mid", data))
                return data

        class Killer(Plugin):
            name = "killer"

            def on_output(self, ctx, data):
                seen.append(("killer", data))
                ctx.self_unload()
                return data

        class Tail(Plugin):
            name = "tail"

            def on_output(self, ctx, data):
                seen.append(("tail", data))
                return data

        h = PluginHost(session, [Mid(), Killer(), Tail()])
        assert h.on_output(b"x") == b"x"
        # 链中卸载发生在整链结束后，本链 tail 仍被调用
        assert seen == [("mid", b"x"), ("killer", b"x"), ("tail", b"x")]
        assert h.names() == ["mid", "tail"]
        seen.clear()
        h.on_output(b"y")
        assert seen == [("mid", b"y"), ("tail", b"y")]


class TestInspectState:
    """返回钩子：命令返回时的一次性状态检查"""

    def test_first_non_none_wins(self, session):
        class S1(Plugin):
            name = "s1"

            def inspect_state(self, ctx):
                return {"state": "Repl"}

        class S2(Plugin):
            name = "s2"

            def inspect_state(self, ctx):
                return {"state": "Running"}

        h = PluginHost(session, [S1(), S2()])
        assert h.inspect_state() == {"state": "Repl"}

    def test_default_none(self, session):
        h = PluginHost(session, [LoggingPlugin()])
        assert h.inspect_state() is None

    def test_exception_isolated(self, session):
        class Bad(Plugin):
            name = "bad"

            def inspect_state(self, ctx):
                raise RuntimeError("boom")

        class Good(Plugin):
            name = "good"

            def inspect_state(self, ctx):
                return {"state": "ok"}

        h = PluginHost(session, [Bad(), Good()])
        assert h.inspect_state() == {"state": "ok"}

    def test_empty_chain(self, session):
        assert PluginHost(session, []).inspect_state() is None


class TestHandleCommand:
    def test_route_to_plugin(self, session):
        p = LoggingPlugin()
        h = PluginHost(session, [p])
        result = h.handle_command("lp", {"command": "x", "args": [1]})
        assert result == {"echo": {"command": "x", "args": [1]}}

    def test_unknown_plugin_or_unhandled(self, session):
        p = LoggingPlugin()
        h = PluginHost(session, [])
        assert h.handle_command("lp", {}) is None
        h.attach(p)
        h.detach("lp")
        assert h.handle_command("lp", {}) is None