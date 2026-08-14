"""files 插件 file_grep / file_glob 命令单元测试 — FilesPlugin.handle_message 链路"""

import os

import pytest

from src.plugins.base import ProcessPluginContext
from config.plugins.files.files_plugin import FilesPlugin
from config.plugins.files.search import grep as grep_impl


class _FakeSession:
    cwd = os.getcwd()


class _FakeManager:
    def get_session(self, session_id):
        return _FakeSession() if session_id == "sid" else None


@pytest.fixture
def plugin():
    return FilesPlugin()


@pytest.fixture
def ctx():
    return ProcessPluginContext(manager=_FakeManager(), plugin=None, io=None)


class TestFileGrepHandler:
    @pytest.fixture(autouse=True)
    def force_fallback(self, monkeypatch):
        monkeypatch.setattr("config.plugins.files.search.grep.RG_EXE", None)

    def test_happy_path(self, plugin, ctx, tmp_path):
        target = tmp_path / "a.txt"
        target.write_text("first line\nneedle here\n", encoding="utf-8")
        resp = plugin.handle_message(ctx, {
            "type": "file_grep", "cwd_session": "sid",
            "pattern": "needle", "path": str(tmp_path)})
        assert resp["commandType"] == "file_grep"
        assert resp["matches"][0]["path"] == str(target)
        assert resp["matches"][0]["lineNumber"] == 2
        assert resp["truncated"] is False

    def test_missing_pattern(self, plugin, ctx, tmp_path):
        resp = plugin.handle_message(ctx, {
            "type": "file_grep", "cwd_session": "sid",
            "pattern": "", "path": str(tmp_path)})
        assert resp["type"] == "error"

    def test_missing_path_defaults_to_session_cwd(self, plugin, ctx, tmp_path, monkeypatch):
        # path 缺省 = 会话 cwd（不再报错）
        called = {}

        def _fake_grep(pattern, path, include=None, literal_text=False):
            called["path"] = path
            return grep_impl.GrepResult([], False, "fallback")
        monkeypatch.setattr(
            "config.plugins.files.files_plugin.grep_impl.grep_files", _fake_grep)
        _FakeSession.cwd = str(tmp_path)
        resp = plugin.handle_message(ctx, {
            "type": "file_grep", "cwd_session": "sid", "pattern": "x"})
        assert called["path"] == str(tmp_path)
        assert resp["commandType"] == "file_grep"
        _FakeSession.cwd = os.getcwd()

    def test_passes_literal_and_include(self, plugin, ctx, tmp_path, monkeypatch):
        called = {}

        def _fake_grep(pattern, path, include=None, literal_text=False):
            called["args"] = (pattern, path, include, literal_text)
            return grep_impl.GrepResult([], False, "fallback")
        monkeypatch.setattr(
            "config.plugins.files.files_plugin.grep_impl.grep_files", _fake_grep)
        resp = plugin.handle_message(ctx, {
            "type": "file_grep", "cwd_session": "sid", "pattern": "a.b",
            "path": str(tmp_path), "include": "*.py", "literal_text": True})
        assert called["args"] == ("a.b", str(tmp_path), "*.py", True)
        assert resp["commandType"] == "file_grep"


class TestFileGlobHandler:
    @pytest.fixture(autouse=True)
    def force_fallback(self, monkeypatch):
        monkeypatch.setattr("config.plugins.files.search.glob_.RG_EXE", None)

    def test_happy_path(self, plugin, ctx, tmp_path):
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        resp = plugin.handle_message(ctx, {
            "type": "file_glob", "cwd_session": "sid",
            "pattern": "*.py", "path": str(tmp_path)})
        assert resp["commandType"] == "file_glob"
        assert resp["files"] == [str(tmp_path / "a.py")]
        assert resp["truncated"] is False

    def test_missing_pattern(self, plugin, ctx, tmp_path):
        resp = plugin.handle_message(ctx, {
            "type": "file_glob", "cwd_session": "sid",
            "pattern": "", "path": str(tmp_path)})
        assert resp["type"] == "error"

    def test_grep_path_not_found(self, plugin, ctx, tmp_path):
        """不存在路径返回错误而非空匹配"""
        resp = plugin.handle_message(ctx, {
            "type": "file_grep", "cwd_session": "sid",
            "pattern": "x", "path": str(tmp_path / "nope")})
        assert resp["type"] == "error"
        assert "not found" in resp["message"]

    def test_glob_path_not_found(self, plugin, ctx, tmp_path):
        """不存在路径返回错误而非空结果"""
        resp = plugin.handle_message(ctx, {
            "type": "file_glob", "cwd_session": "sid",
            "pattern": "*.py", "path": str(tmp_path / "nope")})
        assert resp["type"] == "error"
        assert "not found" in resp["message"]


class TestUploadDownloadIoNone:
    """needs_io=True 但 io 未注入（契约违反）时返回干净错误而非崩溃"""

    def test_upload_io_none_returns_error(self, plugin, ctx, tmp_path):
        resp = plugin.handle_message(ctx, {
            "type": "file_upload_start", "cwd_session": "sid",
            "path": str(tmp_path / "f.txt")})
        assert resp["type"] == "error"
        assert "I/O" in resp["message"]

    def test_download_io_none_returns_error(self, plugin, ctx, tmp_path):
        resp = plugin.handle_message(ctx, {
            "type": "file_download_start", "cwd_session": "sid",
            "path": str(tmp_path / "f.txt")})
        assert resp["type"] == "error"
        assert "I/O" in resp["message"]