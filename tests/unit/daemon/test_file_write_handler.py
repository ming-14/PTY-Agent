"""files 插件 file_write 命令单元测试 — FilesPlugin.handle_message 链路"""

import os

import pytest

from config.plugins.files.settings import settings
from src.plugins.base import ProcessPluginContext
from config.plugins.files.files_plugin import FilesPlugin
from config.plugins.files.state import get_default_store


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


def _call(plugin, ctx, **kw):
    msg = {"type": "file_write", "cwd_session": "sid"}
    msg.update(kw)
    return plugin.handle_message(ctx, msg)


class TestFileWriteHandler:
    def test_new_file(self, plugin, ctx, tmp_path):
        target = tmp_path / "new.txt"
        resp = _call(plugin, ctx, path=str(target), content="hello")
        assert resp["commandType"] == "file_write"
        assert resp["existed"] is False
        assert target.read_text(encoding="utf-8") == "hello"

    def test_reject_unread_existing(self, plugin, ctx, tmp_path):
        get_default_store().reset()
        target = tmp_path / "a.txt"
        target.write_text("old", encoding="utf-8")
        resp = _call(plugin, ctx, path=str(target), content="new")
        assert resp["type"] == "error"
        assert "read" in resp["message"]
        assert target.read_text(encoding="utf-8") == "old"

    def test_missing_content(self, plugin, ctx, tmp_path):
        resp = _call(plugin, ctx, path=str(tmp_path / "x.txt"))
        assert resp["type"] == "error"

    def test_missing_path(self, plugin, ctx):
        resp = _call(plugin, ctx, path="", content="x")
        assert resp["type"] == "error"

    def test_path_too_long(self, plugin, ctx):
        resp = _call(plugin, ctx, path="C:/" + "a" * settings.max_path_len, content="x")
        assert resp["type"] == "error"

    def test_content_too_large(self, plugin, ctx, tmp_path):
        resp = _call(plugin, ctx, path=str(tmp_path / "big.txt"),
                     content="x" * (settings.max_content_len + 1))
        assert resp["type"] == "error"

    def test_io_error_reported(self, plugin, ctx, tmp_path):
        # 父路径是文件时 makedirs 抛 OSError 类异常
        blocker = tmp_path / "blocker"
        blocker.write_text("", encoding="utf-8")
        resp = _call(plugin, ctx, path=str(blocker / "x.txt"), content="x")
        assert resp["type"] == "error"
