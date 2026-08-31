"""file_grep / file_glob 内置 handler 单元测试 — src/daemon/handlers/file_handler.py 链路"""

import os

import pytest

from src.files.search import grep as grep_impl
from src.daemon.handlers.file_handler import _handle_grep, _handle_glob


class _FakeSession:
    cwd = os.getcwd()


class _FakeManager:
    def get_session(self, session_id):
        return _FakeSession() if session_id == "sid" else None


@pytest.fixture
def ctx():
    return type("Ctx", (), {"manager": _FakeManager()})()


class TestFileGrepHandler:
    @pytest.fixture(autouse=True)
    def force_fallback(self, monkeypatch):
        import src.files.settings as _s
        monkeypatch.setattr(_s.settings, "rg_exe", None)

    def test_happy_path(self, ctx, tmp_path):
        target = tmp_path / "a.txt"
        target.write_text("first line\nneedle here\n", encoding="utf-8")
        resp = _handle_grep(ctx, {
            "type": "file_grep", "cwd_session": "sid",
            "pattern": "needle", "path": str(tmp_path)})
        assert resp["commandType"] == "file_grep"
        assert resp["matches"][0]["path"] == str(target)
        assert resp["matches"][0]["lineNumber"] == 2
        assert resp["truncated"] is False

    def test_missing_pattern(self, ctx, tmp_path):
        resp = _handle_grep(ctx, {
            "type": "file_grep", "cwd_session": "sid",
            "pattern": "", "path": str(tmp_path)})
        assert resp["type"] == "error"

    def test_missing_path_defaults_to_session_cwd(self, ctx, tmp_path, monkeypatch):
        # path 缺省 = 会话 cwd（不再报错）
        called = {}

        def _fake_grep(pattern, path, include=None, literal_text=False):
            called["path"] = path
            return grep_impl.GrepResult([], False, "fallback")
        monkeypatch.setattr(
            "src.daemon.handlers.file_handler.grep_impl.grep_files", _fake_grep)
        _FakeSession.cwd = str(tmp_path)
        resp = _handle_grep(ctx, {
            "type": "file_grep", "cwd_session": "sid", "pattern": "x"})
        assert called["path"] == str(tmp_path)
        assert resp["commandType"] == "file_grep"
        _FakeSession.cwd = os.getcwd()

    def test_passes_literal_and_include(self, ctx, tmp_path, monkeypatch):
        called = {}

        def _fake_grep(pattern, path, include=None, literal_text=False):
            called["args"] = (pattern, path, include, literal_text)
            return grep_impl.GrepResult([], False, "fallback")
        monkeypatch.setattr(
            "src.daemon.handlers.file_handler.grep_impl.grep_files", _fake_grep)
        resp = _handle_grep(ctx, {
            "type": "file_grep", "cwd_session": "sid", "pattern": "a.b",
            "path": str(tmp_path), "include": "*.py", "literal_text": True})
        assert called["args"] == ("a.b", str(tmp_path), "*.py", True)
        assert resp["commandType"] == "file_grep"


class TestFileGlobHandler:
    @pytest.fixture(autouse=True)
    def force_fallback(self, monkeypatch):
        import src.files.settings as _s
        monkeypatch.setattr(_s.settings, "rg_exe", None)

    def test_happy_path(self, ctx, tmp_path):
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        resp = _handle_glob(ctx, {
            "type": "file_glob", "cwd_session": "sid",
            "pattern": "*.py", "path": str(tmp_path)})
        assert resp["commandType"] == "file_glob"
        assert resp["files"] == [str(tmp_path / "a.py")]
        assert resp["truncated"] is False

    def test_missing_pattern(self, ctx, tmp_path):
        resp = _handle_glob(ctx, {
            "type": "file_glob", "cwd_session": "sid",
            "pattern": "", "path": str(tmp_path)})
        assert resp["type"] == "error"

    def test_grep_path_not_found(self, ctx, tmp_path):
        """不存在路径返回错误而非空匹配"""
        resp = _handle_grep(ctx, {
            "type": "file_grep", "cwd_session": "sid",
            "pattern": "x", "path": str(tmp_path / "nope")})
        assert resp["type"] == "error"
        assert "not found" in resp["message"]

    def test_glob_path_not_found(self, ctx, tmp_path):
        """不存在路径返回错误而非空结果"""
        resp = _handle_glob(ctx, {
            "type": "file_glob", "cwd_session": "sid",
            "pattern": "*.py", "path": str(tmp_path / "nope")})
        assert resp["type"] == "error"
        assert "not found" in resp["message"]


class TestUploadDownloadParamErrors:
    """upload/download 前置参数校验：cwd_session 缺失/未知、path 缺失"""

    def test_upload_missing_cwd_session(self, ctx, tmp_path):
        from src.daemon.handlers.file_handler import _handle_upload
        sent = {}

        class _Conn:
            def sendall(self, data):
                sent["data"] = data

            def fileno(self):
                return -1

        import json
        _handle_upload(ctx, _Conn(), {"type": "file_upload_start",
                                      "path": str(tmp_path / "f.txt")})
        body = json.loads(sent["data"].decode("utf-8").strip())
        assert body["type"] == "error"
        assert "cwd_session" in body["message"]

    def test_download_missing_cwd_session(self, ctx, tmp_path):
        from src.daemon.handlers.file_handler import _handle_download
        sent = {}

        class _Conn:
            def sendall(self, data):
                sent["data"] = data

            def fileno(self):
                return -1

        import json
        _handle_download(ctx, _Conn(), {"type": "file_download_start",
                                        "path": str(tmp_path / "f.txt")})
        body = json.loads(sent["data"].decode("utf-8").strip())
        assert body["type"] == "error"
        assert "cwd_session" in body["message"]

    def test_download_not_exist(self, ctx):
        from src.daemon.handlers.file_handler import _handle_download
        sent = {}

        class _Conn:
            def sendall(self, data):
                sent["data"] = data

            def fileno(self):
                return -1

        import json
        _handle_download(ctx, _Conn(), {"type": "file_download_start",
                                        "cwd_session": "sid", "path": "/nonexistent/path"})
        body = json.loads(sent["data"].decode("utf-8").strip())
        assert body["type"] == "error"
        assert "does not exist" in body["message"]
