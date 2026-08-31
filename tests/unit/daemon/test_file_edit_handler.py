"""file_edit 内置 handler 单元测试 — src/daemon/handlers/file_handler.py 链路"""

import os

import pytest

from src.files.state import get_default_store
from src.daemon.handlers.file_handler import _handle_edit


class _FakeSession:
    cwd = os.getcwd()


class _FakeManager:
    def get_session(self, session_id):
        return _FakeSession() if session_id == "sid" else None


@pytest.fixture
def ctx():
    return type("Ctx", (), {"manager": _FakeManager()})()


def _call(ctx, **kw):
    msg = {"type": "file_edit", "cwd_session": "sid"}
    msg.update(kw)
    return _handle_edit(ctx, msg)


class TestFileEditHandler:
    def test_create_branch(self, ctx, tmp_path):
        get_default_store().reset()
        target = tmp_path / "new.txt"
        resp = _call(ctx, path=str(target), old="", new="hello")
        assert resp["commandType"] == "file_edit"
        assert target.read_text(encoding="utf-8") == "hello"

    def test_missing_old_new(self, ctx, tmp_path):
        resp = _call(ctx, path=str(tmp_path / "x.txt"), old="a")
        assert resp["type"] == "error"

    def test_edit_requires_content_type(self, ctx, tmp_path):
        resp = _call(ctx, path=str(tmp_path / "x.txt"), old=5, new="")
        assert resp["type"] == "error"

    def test_missing_path(self, ctx):
        resp = _call(ctx, path="", old="a", new="b")
        assert resp["type"] == "error"
