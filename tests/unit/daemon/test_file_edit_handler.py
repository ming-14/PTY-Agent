"""file_edit handler 单元测试 — file edit 命令链路"""

import json

import pytest
import os

from src.daemon.handlers.base import HandlerContext
from src.daemon.handlers.file_edit_handler import FileEditHandler
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
    return FileEditHandler()


@pytest.fixture
def ctx():
    return HandlerContext(manager=_FakeManager())


def _last_response(conn: _CollectConn) -> dict:
    assert conn.sent, "handler 未发送响应"
    return json.loads(conn.sent[-1].decode("utf-8"))


class TestFileEditHandler:
    def test_create_branch(self, handler, ctx, tmp_path):
        get_default_store().reset()
        target = tmp_path / "new.txt"
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_edit", "cwd_session": "sid", "path": str(target), "old": "", "new": "hello"})
        resp = _last_response(conn)
        assert resp["commandType"] == "file_edit"
        assert target.read_text(encoding="utf-8") == "hello"

    def test_missing_old_new(self, handler, ctx, tmp_path):
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_edit", "cwd_session": "sid", "path": str(tmp_path / "x.txt"), "old": "a"})
        resp = _last_response(conn)
        assert resp["type"] == "error"

    def test_edit_requires_content_type(self, handler, ctx, tmp_path):
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_edit", "cwd_session": "sid", "path": str(tmp_path / "x.txt"), "old": 5, "new": ""})
        resp = _last_response(conn)
        assert resp["type"] == "error"

    def test_missing_path(self, handler, ctx):
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_edit", "cwd_session": "sid", "path": "", "old": "a", "new": "b"})
        resp = _last_response(conn)
        assert resp["type"] == "error"