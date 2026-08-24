"""事件总线单测 — 主题通配、订阅/退订、异常隔离"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.events import EventBus, match_topic  # noqa: E402


class TestMatchTopic:
    @pytest.mark.parametrize("pattern, topic, expected", [
        ("session.created", "session.created", True),
        ("session.created", "session.ended", False),
        ("session.created", "session", False),
        ("session.*", "session.created", True),
        ("session.*", "session.event.x", False),
        ("session.>", "session.created", True),
        ("session.>", "session.event.process_spawn", True),
        ("session.>", "session", True),
        ("plugin.enabled", "session.created", False),
        ("a.b.c", "a.b.c.d", False),
        ("a.b.>", "a.b.c.d", True),
        (">", "anything.at.all", True),
    ])
    def test_patterns(self, pattern, topic, expected):
        assert match_topic(pattern, topic) is expected


class TestEventBus:
    def test_publish_delivers_to_matching(self):
        bus = EventBus()
        got = []
        bus.subscribe("session.*", lambda e: got.append(("s", e.topic)))
        bus.subscribe("session.created", lambda e: got.append(("c", e.topic)))
        bus.subscribe("plugin.enabled", lambda e: got.append(("p", e.topic)))
        bus.publish("session.created", {"id": "s1"})
        assert sorted(got) == [("c", "session.created"), ("s", "session.created")]

    def test_event_fields(self):
        bus = EventBus()
        got = []
        bus.subscribe("a", lambda e: got.append(e))
        bus.publish("a", {"x": 1}, source="test")
        e = got[0]
        assert e.topic == "a"
        assert e.source == "test"
        assert e.payload == {"x": 1}
        assert e.timestamp > 0
        d = e.to_dict()
        assert d["topic"] == "a" and d["payload"] == {"x": 1}

    def test_unsubscribe(self):
        bus = EventBus()
        got = []

        def cb(e):
            got.append(e.topic)

        bus.subscribe("a", cb)
        bus.publish("a")
        bus.unsubscribe("a", cb)
        bus.publish("a")
        assert got == ["a"]

    def test_subscriber_exception_isolated(self):
        bus = EventBus()
        got = []

        def boom(e):
            raise RuntimeError("boom")

        bus.subscribe("a", boom)
        bus.subscribe("a", lambda e: got.append(e.topic))
        bus.publish("a")
        assert got == ["a"]
