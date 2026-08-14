"""files 插件 file_edit 命令单元测试 — FilesPlugin.handle_message 链路"""

import os

import pytest

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
    msg = {"type": "file_edit", "cwd_session": "sid"}
    msg.update(kw)
    return plugin.handle_message(ctx, msg)


class TestFileEditHandler:
    def test_create_branch(self, plugin, ctx, tmp_path):
        get_default_store().reset()
        target = tmp_path / "new.txt"
        resp = _call(plugin, ctx, path=str(target), old="", new="hello")
        assert resp["commandType"] == "file_edit"
        assert target.read_text(encoding="utf-8") == "hello"

    def test_missing_old_new(self, plugin, ctx, tmp_path):
        resp = _call(plugin, ctx, path=str(tmp_path / "x.txt"), old="a")
        assert resp["type"] == "error"

    def test_edit_requires_content_type(self, plugin, ctx, tmp_path):
        resp = _call(plugin, ctx, path=str(tmp_path / "x.txt"), old=5, new="")
        assert resp["type"] == "error"

    def test_missing_path(self, plugin, ctx):
        resp = _call(plugin, ctx, path="", old="a", new="b")
        assert resp["type"] == "error"