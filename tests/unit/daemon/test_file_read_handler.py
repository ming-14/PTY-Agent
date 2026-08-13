"""file_read handler 单元测试 — file read 命令链路

用收集 sendall 的 mock conn 直接调用 handler，不走真实 socket。
"""

import json
import os
import pytest

from src.daemon.handlers.base import HandlerContext
from src.daemon.handlers.file_read_handler import FileReadHandler
from src.files import get_default_store


class _FakeSession:
    cwd = os.getcwd()


class _FakeManager:
    def get_session(self, session_id):
        return _FakeSession() if session_id == "sid" else None


class _CollectConn:
    def __init__(self):
        self.sent = []
        self._closed = False

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self._closed = True

    def fileno(self):
        return -1

    def settimeout(self, t):
        pass


@pytest.fixture
def handler():
    return FileReadHandler()


@pytest.fixture
def ctx():
    return HandlerContext(manager=_FakeManager())


def _msg(**kw):
    """带 cwd_session 的 handler 消息构造"""
    m = {"type": "file_read", "cwd_session": "sid"}
    m.update(kw)
    return m


def _last_response(conn: _CollectConn) -> dict:
    assert conn.sent, "handler 未发送响应"
    return json.loads(conn.sent[-1].decode("utf-8"))


@pytest.fixture
def text_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("line0\nline1\nline2\n", encoding="utf-8")
    return str(p)


class TestFileReadHandler:
    def test_happy_path(self, handler, ctx, text_file):
        store = get_default_store()
        store.reset()
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_read", "cwd_session": "sid", "path": text_file})
        resp = _last_response(conn)
        assert resp["commandType"] == "file_read"
        assert resp["path"] == text_file
        assert "     1|line1" in resp["content"]
        assert resp["totalLines"] == 3
        assert resp["truncated"] is False
        assert store.last_read(text_file) is not None

    def test_offset_limit(self, handler, ctx, text_file):
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_read", "cwd_session": "sid", "path": text_file, "offset": 1, "limit": 1})
        resp = _last_response(conn)
        assert "line0" not in resp["content"]
        assert "line1" in resp["content"]
        assert resp["truncated"] is True

    def test_missing_path(self, handler, ctx):
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_read", "cwd_session": "sid", "path": ""})
        resp = _last_response(conn)
        assert resp["type"] == "error"

    def test_not_found_with_suggestion(self, handler, ctx, tmp_path):
        (tmp_path / "user_info.py").write_text("", encoding="utf-8")
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_read", "cwd_session": "sid", "path": str(tmp_path / "user_info")})
        resp = _last_response(conn)
        assert resp["type"] == "error"
        assert "Did you mean" in resp["message"]

    def test_not_found_no_hint(self, handler, ctx, tmp_path):
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_read", "cwd_session": "sid", "path": str(tmp_path / "zzz")})
        resp = _last_response(conn)
        assert resp["type"] == "error"
        assert "File not found" in resp["message"]

    def test_bad_offset_type(self, handler, ctx, text_file):
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_read", "cwd_session": "sid", "path": text_file, "offset": "abc"})
        resp = _last_response(conn)
        assert resp["type"] == "error"

    def test_too_long_path(self, handler, ctx, text_file):
        conn = _CollectConn()
        from src.config.files import MAX_PATH_LEN
        handler.handle(ctx, conn, {"type": "file_read", "cwd_session": "sid", "path": text_file + "x" * MAX_PATH_LEN})
        resp = _last_response(conn)
        assert resp["type"] == "error"

    def test_missing_cwd_session(self, handler, ctx, text_file):
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_read", "path": text_file})
        resp = _last_response(conn)
        assert resp["type"] == "error"
        assert "cwd_session" in resp["message"]

    def test_unknown_cwd_session(self, handler, ctx, text_file):
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_read", "cwd_session": "nope", "path": text_file})
        resp = _last_response(conn)
        assert resp["type"] == "error"
        assert "not found" in resp["message"]

    def test_relative_path_resolved_against_session_cwd(self, handler, ctx, tmp_path):
        (tmp_path / "rel.txt").write_text("rel\n", encoding="utf-8")
        _FakeSession.cwd = str(tmp_path)
        conn = _CollectConn()
        handler.handle(ctx, conn, _msg(path="rel.txt"))
        resp = _last_response(conn)
        assert resp["commandType"] == "file_read"
        assert resp["path"] == os.path.normpath(os.path.join(str(tmp_path), "rel.txt"))
        _FakeSession.cwd = os.getcwd()

    def test_image_rejected(self, handler, ctx, tmp_path):
        p = tmp_path / "a.png"
        p.write_bytes(b"fake")
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_read", "cwd_session": "sid", "path": str(p)})
        resp = _last_response(conn)
        assert resp["type"] == "error"
        assert "image" in resp["message"]