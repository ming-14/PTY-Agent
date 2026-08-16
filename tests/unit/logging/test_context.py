"""测试上下文绑定"""
from src.logging import bind, clear, get_context, unbind


def test_bind_and_get_context():
    """bind 后 get_context 返回绑定的字段"""
    token = bind(session_id="abc123")
    try:
        ctx = get_context()
        assert ctx["session_id"] == "abc123"
    finally:
        unbind(token)


def test_unbind_restores_previous():
    """unbind 后恢复到 bind 前的状态"""
    assert get_context() == {}

    token = bind(session_id="abc")
    try:
        assert get_context()["session_id"] == "abc"
    finally:
        unbind(token)

    assert get_context() == {}


def test_bind_multiple_fields():
    """bind 多个字段"""
    token = bind(session_id="s1", connection_id="c1", request_id="r1")
    try:
        ctx = get_context()
        assert ctx["session_id"] == "s1"
        assert ctx["connection_id"] == "c1"
        assert ctx["request_id"] == "r1"
    finally:
        unbind(token)


def test_bind_nested():
    """嵌套 bind 合并字段"""
    token1 = bind(session_id="s1")
    try:
        token2 = bind(connection_id="c1")
        try:
            ctx = get_context()
            assert ctx["session_id"] == "s1"
            assert ctx["connection_id"] == "c1"
        finally:
            unbind(token2)

        # unbind token2 后只剩 session_id
        ctx = get_context()
        assert ctx == {"session_id": "s1"}
    finally:
        unbind(token1)


def test_bind_overwrite():
    """同名字段后绑定的覆盖先绑定的"""
    token1 = bind(session_id="old")
    try:
        token2 = bind(session_id="new")
        try:
            assert get_context()["session_id"] == "new"
        finally:
            unbind(token2)
        assert get_context()["session_id"] == "old"
    finally:
        unbind(token1)


def test_clear():
    """clear 清除所有字段"""
    bind(session_id="abc")
    clear()
    assert get_context() == {}


def test_get_context_empty():
    """无绑定时 get_context 返回空 dict"""
    assert get_context() == {}
