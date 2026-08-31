"""file_write 内置 handler 单元测试 — src/daemon/handlers/file_handler.py 链路"""

import os

import pytest

from src.files.settings import settings
from src.files.state import get_default_store
from src.daemon.handlers.file_handler import _handle_write


class _FakeSession:
    cwd = os.getcwd()


class _FakeManager:
    def get_session(self, session_id):
        return _FakeSession() if session_id == "sid" else None


@pytest.fixture
def ctx():
    return type("Ctx", (), {"manager": _FakeManager()})()


def _call(ctx, **kw):
    msg = {"type": "file_write", "cwd_session": "sid"}
    msg.update(kw)
    return _handle_write(ctx, msg)


class TestFileWriteHandler:
    def test_new_file(self, ctx, tmp_path):
        target = tmp_path / "new.txt"
        resp = _call(ctx, path=str(target), content="hello")
        assert resp["commandType"] == "file_write"
        assert resp["existed"] is False
        assert target.read_text(encoding="utf-8") == "hello"

    def test_reject_unread_existing(self, ctx, tmp_path):
        get_default_store().reset()
        target = tmp_path / "a.txt"
        target.write_text("old", encoding="utf-8")
        resp = _call(ctx, path=str(target), content="new")
        assert resp["type"] == "error"
        assert "read" in resp["message"]
        assert target.read_text(encoding="utf-8") == "old"

    def test_missing_content(self, ctx, tmp_path):
        resp = _call(ctx, path=str(tmp_path / "x.txt"))
        assert resp["type"] == "error"

    def test_missing_path(self, ctx):
        resp = _call(ctx, path="", content="x")
        assert resp["type"] == "error"

    def test_path_too_long(self, ctx):
        resp = _call(ctx, path="C:/" + "a" * settings.max_path_len, content="x")
        assert resp["type"] == "error"

    def test_content_too_large(self, ctx, tmp_path):
        resp = _call(ctx, path=str(tmp_path / "big.txt"),
                     content="x" * (settings.max_content_len + 1))
        assert resp["type"] == "error"

    def test_io_error_reported(self, ctx, tmp_path):
        # 父路径是文件时 makedirs 抛 OSError 类异常
        blocker = tmp_path / "blocker"
        blocker.write_text("", encoding="utf-8")
        resp = _call(ctx, path=str(blocker / "x.txt"), content="x")
        assert resp["type"] == "error"
