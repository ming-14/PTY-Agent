"""file_write handler 单元测试 — file write 命令链路"""

import json
import os
import time

import pytest

from src.config.files import MAX_CONTENT_LEN, MAX_PATH_LEN
from src.daemon.handlers.base import HandlerContext
from src.daemon.handlers.file_write_handler import FileWriteHandler
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
    return FileWriteHandler()


@pytest.fixture
def ctx():
    return HandlerContext(manager=_FakeManager())


def _last_response(conn: _CollectConn) -> dict:
    assert conn.sent, "handler 未发送响应"
    return json.loads(conn.sent[-1].decode("utf-8"))


class TestFileWriteHandler:
    def test_new_file(self, handler, ctx, tmp_path):
        target = tmp_path / "new.txt"
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_write", "cwd_session": "sid", "path": str(target), "content": "hello"})
        resp = _last_response(conn)
        assert resp["commandType"] == "file_write"
        assert resp["existed"] is False
        assert target.read_text(encoding="utf-8") == "hello"

    def test_reject_unread_existing(self, handler, ctx, tmp_path):
        get_default_store().reset()
        target = tmp_path / "a.txt"
        target.write_text("old", encoding="utf-8")
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_write", "cwd_session": "sid", "path": str(target), "content": "new"})
        resp = _last_response(conn)
        assert resp["type"] == "error"
        assert "read" in resp["message"]
        assert target.read_text(encoding="utf-8") == "old"

    def test_missing_content(self, handler, ctx, tmp_path):
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_write", "cwd_session": "sid", "path": str(tmp_path / "x.txt")})
        resp = _last_response(conn)
        assert resp["type"] == "error"

    def test_missing_path(self, handler, ctx):
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_write", "cwd_session": "sid", "path": "", "content": "x"})
        resp = _last_response(conn)
        assert resp["type"] == "error"

    def test_path_too_long(self, handler, ctx):
        conn = _CollectConn()
        handler.handle(ctx, conn,
                       {"type": "file_write", "cwd_session": "sid", "path": "C:/" + "a" * MAX_PATH_LEN, "content": "x"})
        resp = _last_response(conn)
        assert resp["type"] == "error"

    def test_content_too_large(self, handler, ctx, tmp_path):
        conn = _CollectConn()
        handler.handle(ctx, conn,
                       {"type": "file_write", "cwd_session": "sid", "path": str(tmp_path / "big.txt"),
                        "content": "x" * (MAX_CONTENT_LEN + 1)})
        resp = _last_response(conn)
        assert resp["type"] == "error"

    def test_io_error_reported(self, handler, ctx, tmp_path):
        conn = _CollectConn()
        # 父路径是文件时 makedirs 抛 OSError 类异常
        blocker = tmp_path / "blocker"
        blocker.write_text("", encoding="utf-8")
        handler.handle(ctx, conn,
                       {"type": "file_write", "cwd_session": "sid", "path": str(blocker / "x.txt"), "content": "x"})
        resp = _last_response(conn)
        assert resp["type"] == "error"