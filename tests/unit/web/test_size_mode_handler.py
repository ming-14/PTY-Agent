"""SetSizeModeHandler 单元测试（P4：自适应锁校验 + scrollback 广播）。"""

import pytest

from src.web.application.adaptive_lock import AdaptiveLockService
from src.web.application.handlers.size_mode import SetSizeModeHandler


class _Session:
    def __init__(self, uid="uid-1", sid="s1"):
        self.uid = uid
        self.id = sid
        self.cols = 100
        self.rows = 30
        self.resize_calls = []

    def resize(self, cols, rows):
        self.resize_calls.append((cols, rows))
        return ("SNAP", "SB-LINES")


class _Executor:
    async def run(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)


class _Publisher:
    def __init__(self):
        self.resized = []
        self.mode_changed = []

    def publish_session_resized(self, **kw):
        self.resized.append(kw)

    def publish_size_mode_changed(self, **kw):
        self.mode_changed.append(kw)


class _Channel:
    def __init__(self):
        self.id = 42


class _Conn:
    def __init__(self, client_uid="client-a"):
        self.client_uid = client_uid


def _make_ctx(lock, session, client_uid="client-a"):
    class _Repo:
        def get_by_uid(self, uid):
            return session if session and session.uid == uid else None

        def resolve_sid(self, sid):
            return session.uid if session and session.id == sid else None

    ctx = type("Ctx", (), {})()
    ctx.session_repo = _Repo()
    ctx.adaptive_lock = lock
    ctx.executor = _Executor()
    ctx.publisher = _Publisher()
    ctx.channel = _Channel()
    ctx.connection = _Conn(client_uid)
    return ctx


@pytest.mark.asyncio
async def test_fixed_mode_rejected_when_other_owner():
    lock = AdaptiveLockService()
    lock.acquire("uid-1", "client-b")  # 他人持锁
    session = _Session()
    ctx = _make_ctx(lock, session, client_uid="client-a")
    handler = SetSizeModeHandler()
    msgs = await handler.handle(ctx, {
        "sessionUid": "uid-1", "mode": "fixed", "cols": 120, "rows": 40,
    })
    assert msgs[0]["type"] == "error"
    assert msgs[0].get("code") == "adaptive_locked"
    assert session.resize_calls == [], "被锁时不应执行 resize"
    assert lock.get_owner("uid-1") == "client-b", "锁不应被释放"


@pytest.mark.asyncio
async def test_fixed_mode_allowed_for_owner():
    lock = AdaptiveLockService()
    lock.acquire("uid-1", "client-a")  # 本端持锁
    session = _Session()
    ctx = _make_ctx(lock, session, client_uid="client-a")
    handler = SetSizeModeHandler()
    msgs = await handler.handle(ctx, {
        "sessionUid": "uid-1", "mode": "fixed", "cols": 120, "rows": 40,
    })
    assert msgs[0]["type"] == "size_mode_ack"
    assert msgs[0]["mode"] == "fixed"
    assert session.resize_calls == [(120, 40)]
    assert lock.get_owner("uid-1") is None, "持锁者切 fixed 应释放锁"
    # 广播携带 scrollback
    assert ctx.publisher.resized, "应广播 session_resized"
    assert ctx.publisher.resized[0]["scrollback"] == "SB-LINES"
    assert ctx.publisher.resized[0]["snapshot"] == "SNAP"


@pytest.mark.asyncio
async def test_custom_mode_allowed_without_owner():
    lock = AdaptiveLockService()
    session = _Session()
    ctx = _make_ctx(lock, session, client_uid="client-a")
    handler = SetSizeModeHandler()
    msgs = await handler.handle(ctx, {
        "sessionUid": "uid-1", "mode": "custom", "cols": 90, "rows": 25,
    })
    assert msgs[0]["type"] == "size_mode_ack"
    assert session.resize_calls == [(90, 25)]
    assert ctx.publisher.resized[0]["scrollback"] == "SB-LINES"


@pytest.mark.asyncio
async def test_adaptive_acquire_ignores_other_owner():
    """adaptive 模式是锁夺取语义：其他持锁者可接管（与 fixed/custom 不同）"""
    lock = AdaptiveLockService()
    lock.acquire("uid-1", "client-b")
    session = _Session()
    ctx = _make_ctx(lock, session, client_uid="client-a")
    handler = SetSizeModeHandler()
    msgs = await handler.handle(ctx, {
        "sessionUid": "uid-1", "mode": "adaptive",
    })
    assert msgs[0]["type"] == "size_mode_ack"
    assert lock.get_owner("uid-1") == "client-a", "adaptive 应夺取锁"


@pytest.mark.asyncio
async def test_missing_session_returns_error():
    lock = AdaptiveLockService()
    ctx = _make_ctx(lock, None, client_uid="client-a")
    handler = SetSizeModeHandler()
    msgs = await handler.handle(ctx, {
        "sessionUid": "nope", "mode": "fixed", "cols": 1, "rows": 1,
    })
    assert msgs[0]["type"] == "error"
