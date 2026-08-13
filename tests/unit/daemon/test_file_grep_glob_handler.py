"""file_grep / file_glob handler 单元测试"""

import json

import pytest
import os

from src.daemon.handlers.base import HandlerContext
from src.daemon.handlers.file_grep_handler import FileGrepHandler
from src.daemon.handlers.file_glob_handler import FileGlobHandler
from src.files.search import grep, glob_


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


def _last_response(conn: _CollectConn) -> dict:
    assert conn.sent, "handler 未发送响应"
    return json.loads(conn.sent[-1].decode("utf-8"))


@pytest.fixture
def ctx():
    return HandlerContext(manager=_FakeManager())


class TestFileGrepHandler:
    @pytest.fixture(autouse=True)
    def force_fallback(self, monkeypatch):
        monkeypatch.setattr("src.files.search.grep.RG_EXE", None)

    def test_happy_path(self, ctx, tmp_path):
        handler = FileGrepHandler()
        target = tmp_path / "a.txt"
        target.write_text("first line\nneedle here\n", encoding="utf-8")
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_grep", "cwd_session": "sid", "pattern": "needle", "path": str(tmp_path)})
        resp = _last_response(conn)
        assert resp["commandType"] == "file_grep"
        assert resp["matches"][0]["path"] == str(target)
        assert resp["matches"][0]["lineNumber"] == 2
        assert resp["truncated"] is False

    def test_missing_pattern(self, ctx, tmp_path):
        conn = _CollectConn()
        FileGrepHandler().handle(ctx, conn, {"type": "file_grep", "cwd_session": "sid", "pattern": "", "path": str(tmp_path)})
        resp = _last_response(conn)
        assert resp["type"] == "error"

    def test_missing_path_defaults_to_session_cwd(self, ctx, tmp_path, monkeypatch):
        # path 缺省 = 会话 cwd（不再报错）
        called = {}

        def _fake_grep(pattern, path, include=None, literal_text=False):
            called["path"] = path
            from src.files.search.grep import GrepResult
            return GrepResult([], False, "fallback")
        monkeypatch.setattr(
            "src.daemon.handlers.file_grep_handler.grep_files", _fake_grep)
        _FakeSession.cwd = str(tmp_path)
        conn = _CollectConn()
        FileGrepHandler().handle(ctx, conn, {"type": "file_grep", "cwd_session": "sid", "pattern": "x"})
        assert called["path"] == str(tmp_path)
        _FakeSession.cwd = os.getcwd()

    def test_passes_literal_and_include(self, ctx, tmp_path, monkeypatch):
        called = {}

        def _fake_grep(pattern, path, include=None, literal_text=False):
            called["args"] = (pattern, path, include, literal_text)
            from src.files.search.grep import GrepResult
            return GrepResult([], False, "fallback")
        monkeypatch.setattr(
            "src.daemon.handlers.file_grep_handler.grep_files", _fake_grep)
        conn = _CollectConn()
        FileGrepHandler().handle(ctx, conn, {
            "type": "file_grep", "cwd_session": "sid", "pattern": "a.b", "path": str(tmp_path),
            "include": "*.py", "literal_text": True})
        assert called["args"] == ("a.b", str(tmp_path), "*.py", True)


class TestFileGlobHandler:
    @pytest.fixture(autouse=True)
    def force_fallback(self, monkeypatch):
        monkeypatch.setattr("src.files.search.glob_.RG_EXE", None)

    def test_happy_path(self, ctx, tmp_path):
        handler = FileGlobHandler()
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        conn = _CollectConn()
        handler.handle(ctx, conn, {"type": "file_glob", "cwd_session": "sid", "pattern": "*.py", "path": str(tmp_path)})
        resp = _last_response(conn)
        assert resp["commandType"] == "file_glob"
        assert resp["files"] == [str(tmp_path / "a.py")]
        assert resp["truncated"] is False

    def test_missing_pattern(self, ctx, tmp_path):
        conn = _CollectConn()
        FileGlobHandler().handle(ctx, conn, {"type": "file_glob", "cwd_session": "sid", "pattern": "", "path": str(tmp_path)})
        resp = _last_response(conn)
        assert resp["type"] == "error"