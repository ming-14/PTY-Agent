"""file_read 内置 handler 单元测试 — src/daemon/handlers/file_handler.py 链路

直接调用 _handle_read 断言返回 dict（不走真实 socket）。
"""

import os

import pytest

from src.files.settings import settings
from src.files.state import get_default_store
from src.daemon.handlers.file_handler import _handle_read


class _FakeSession:
    cwd = os.getcwd()


class _FakeManager:
    def get_session(self, session_id):
        return _FakeSession() if session_id == "sid" else None


@pytest.fixture
def ctx():
    return type("Ctx", (), {"manager": _FakeManager()})()


def _call(ctx, **kw):
    """带 cwd_session 的 file_read 消息构造并调用"""
    msg = {"type": "file_read", "cwd_session": "sid"}
    msg.update(kw)
    return _handle_read(ctx, msg)


@pytest.fixture
def text_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("line0\nline1\nline2\n", encoding="utf-8")
    return str(p)


class TestFileReadHandler:
    def test_happy_path(self, ctx, text_file):
        store = get_default_store()
        store.reset()
        resp = _call(ctx, path=text_file)
        assert resp["commandType"] == "file_read"
        assert resp["path"] == text_file
        assert "     1|line1" in resp["content"]
        assert resp["totalLines"] == 3
        assert resp["truncated"] is False
        assert store.last_read(text_file) is not None

    def test_offset_limit(self, ctx, text_file):
        resp = _call(ctx, path=text_file, offset=1, limit=1)
        assert "line0" not in resp["content"]
        assert "line1" in resp["content"]
        assert resp["truncated"] is True

    def test_missing_path(self, ctx):
        resp = _call(ctx, path="")
        assert resp["type"] == "error"

    def test_not_found_with_suggestion(self, ctx, tmp_path):
        (tmp_path / "user_info.py").write_text("", encoding="utf-8")
        resp = _call(ctx, path=str(tmp_path / "user_info"))
        assert resp["type"] == "error"
        assert "Did you mean" in resp["message"]

    def test_not_found_no_hint(self, ctx, tmp_path):
        resp = _call(ctx, path=str(tmp_path / "zzz"))
        assert resp["type"] == "error"
        assert "File not found" in resp["message"]

    def test_bad_offset_type(self, ctx, text_file):
        resp = _call(ctx, path=text_file, offset="abc")
        assert resp["type"] == "error"

    def test_too_long_path(self, ctx, text_file):
        resp = _call(ctx, path=text_file + "x" * settings.max_path_len)
        assert resp["type"] == "error"

    def test_missing_cwd_session(self, ctx, text_file):
        resp = _handle_read(ctx, {"type": "file_read", "path": text_file})
        assert resp["type"] == "error"
        assert "cwd_session" in resp["message"]

    def test_unknown_cwd_session(self, ctx, text_file):
        resp = _handle_read(ctx, {"type": "file_read", "cwd_session": "nope", "path": text_file})
        assert resp["type"] == "error"
        assert "not found" in resp["message"]

    def test_relative_path_resolved_against_session_cwd(self, ctx, tmp_path):
        (tmp_path / "rel.txt").write_text("rel\n", encoding="utf-8")
        _FakeSession.cwd = str(tmp_path)
        resp = _call(ctx, path="rel.txt")
        assert resp["commandType"] == "file_read"
        assert resp["path"] == os.path.normpath(os.path.join(str(tmp_path), "rel.txt"))
        _FakeSession.cwd = os.getcwd()

    def test_image_rejected(self, ctx, tmp_path):
        p = tmp_path / "a.png"
        p.write_bytes(b"fake")
        resp = _call(ctx, path=str(p))
        assert resp["type"] == "error"
        assert "image" in resp["message"]
