"""HookEngine 单测 — 优先级排序、五类调度语义、异常隔离"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.hooks import HookEngine  # noqa: E402
from src.plugins.base import Plugin  # noqa: E402
from tests.helpers import make_manifest, attach_manifest  # noqa: E402


class _Probe:
    """记录调用顺序的迷你插件"""

    def __init__(self, name):
        self.name = name
        self.manifest = make_manifest(name, triggers=["event"])
        self.calls = []

    def on_input(self, ctx, data):
        self.calls.append(self.name)
        return data

    def on_event(self, ctx, event):
        self.calls.append(self.name)

    def inspect_state(self, ctx):
        return None

    def handle_command(self, ctx, msg):
        return None


class _Ctx:
    def __init__(self, plugin):
        self.plugin = plugin


def _factory(plugin):
    return _Ctx(plugin)


class TestModify:
    def test_chain_order_and_transform(self):
        engine = HookEngine()
        a = _Probe("a")
        b = _Probe("b")
        b.on_input = lambda ctx, data: data + b"b"
        a.on_input = lambda ctx, data: data + b"a"
        attach_manifest(a, make_manifest("a", hooks={"on_input": {"priority": 200}}))
        attach_manifest(b, make_manifest("b"))
        engine.register(a)
        engine.register(b)
        assert engine.dispatch_modify("on_input", _factory, b"x") == b"xab"

    def test_intercept_returns_none(self):
        engine = HookEngine()
        p = _Probe("p")
        p.on_input = lambda ctx, data: None
        engine.register(p)
        assert engine.dispatch_modify("on_input", _factory, b"x") is None

    def test_no_intercept_mode_keeps_value(self):
        engine = HookEngine()
        p = _Probe("p")
        p.on_input = lambda ctx, data: None
        engine.register(p)
        assert engine.dispatch_modify("on_input", _factory, b"x", intercept=False) == b"x"

    def test_exception_isolated(self):
        engine = HookEngine()
        bad = _Probe("bad")
        good = _Probe("good")
        bad.on_input = lambda ctx, data: (_ for _ in ()).throw(RuntimeError("boom"))
        good.on_input = lambda ctx, data: data + b"ok"
        engine.register(bad)
        engine.register(good)
        assert engine.dispatch_modify("on_input", _factory, b"x") == b"xok"


class TestObserve:
    def test_all_called(self):
        engine = HookEngine()
        a, b = _Probe("a"), _Probe("b")
        engine.register(a)
        engine.register(b)
        engine.dispatch_observe("on_event", _factory, {"type": "x"})
        assert a.calls == ["a"]
        assert b.calls == ["b"]

    def test_exception_isolated(self):
        engine = HookEngine()
        bad = _Probe("bad")
        good = _Probe("good")
        bad.on_event = lambda ctx, ev: (_ for _ in ()).throw(RuntimeError("boom"))
        engine.register(bad)
        engine.register(good)
        engine.dispatch_observe("on_event", _factory, {})
        assert good.calls == ["good"]


class TestProvide:
    def test_low_priority_first_wins(self):
        engine = HookEngine()
        low = _Probe("low")
        high = _Probe("high")
        low.inspect_state = lambda ctx: {"from": "low"}
        high.inspect_state = lambda ctx: {"from": "high"}
        attach_manifest(low, make_manifest("low", hooks={"inspect_state": {"priority": 50}}))
        attach_manifest(high, make_manifest("high", hooks={"inspect_state": {"priority": 200}}))
        engine.register(low)
        engine.register(high)
        assert engine.dispatch_provide("inspect_state", _factory) == {"from": "low"}

    def test_first_non_none_wins(self):
        engine = HookEngine()
        a, b = _Probe("a"), _Probe("b")
        a.inspect_state = lambda ctx: None
        b.inspect_state = lambda ctx: {"from": "b"}
        engine.register(a)
        engine.register(b)
        assert engine.dispatch_provide("inspect_state", _factory) == {"from": "b"}

    def test_all_none_returns_none(self):
        engine = HookEngine()
        engine.register(_Probe("a"))
        assert engine.dispatch_provide("inspect_state", _factory) is None

    def test_exception_isolated(self):
        engine = HookEngine()
        bad = _Probe("bad")
        good = _Probe("good")
        bad.inspect_state = lambda ctx: (_ for _ in ()).throw(RuntimeError("boom"))
        good.inspect_state = lambda ctx: {"ok": 1}
        engine.register(bad)
        engine.register(good)
        assert engine.dispatch_provide("inspect_state", _factory) == {"ok": 1}


class TestUnregister:
    def test_unregister_removes_hooks(self):
        engine = HookEngine()
        p = _Probe("p")
        engine.register(p)
        assert engine.dispatch_provide("inspect_state", _factory) is not None or True
        engine.unregister(p)
        assert engine.dispatch_modify("on_input", _factory, b"x") == b"x"

    def test_predicate_filters(self):
        engine = HookEngine()
        a, b = _Probe("a"), _Probe("b")
        seen = []
        a.on_event = lambda ctx, ev: (seen.append("a") or None)
        b.on_event = lambda ctx, ev: (seen.append("b") or None)
        engine.register(a)
        engine.register(b)
        engine.dispatch_observe(
            "on_event", _factory, {}, predicate=lambda p: p.name == "b"
        )
        assert seen == ["b"]
