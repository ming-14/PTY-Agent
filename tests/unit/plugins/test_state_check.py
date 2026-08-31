"""config/plugins/state_check 插件单测 — 响应装饰 + CLI 渲染 + _detect 检测逻辑"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from src.plugins.loader import load_plugin_dir
from src.client.cli_plugins import CliPluginHost
from src.plugins.host import PluginHost

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_PLUGIN_PATH = os.path.join(_PROJECT_ROOT, "config", "plugins", "state_check")


# ═══════════════════════════════════════════════════════════
# 插件声明
# ═══════════════════════════════════════════════════════════

class TestPluginDecl:
    """插件声明：kind / manifest 正确加载"""

    def test_manifest_loads(self):
        loaded = load_plugin_dir(_PLUGIN_PATH)
        assert loaded is not None
        m = loaded.manifest
        assert m.id == "state_check"
        assert m.kind == ["process", "cli"]
        assert m.decorate_types == ["list"]
        assert m.auto_mount == ["list"]

    def test_hooks_declared(self):
        loaded = load_plugin_dir(_PLUGIN_PATH)
        cls = loaded.cls
        from src.plugins.base import Plugin
        has_decorate = getattr(cls, "decorate_response") is not getattr(Plugin, "decorate_response")
        has_render = getattr(cls, "render_response") is not getattr(Plugin, "render_response")
        assert has_decorate
        assert has_render

    def test_no_trigger_declaration(self):
        loaded = load_plugin_dir(_PLUGIN_PATH)
        assert loaded.manifest.triggers == []

    def test_two_hooks_only(self):
        loaded = load_plugin_dir(_PLUGIN_PATH)
        hooks = dict(loaded.manifest.hooks)
        assert set(hooks) == {"decorate_response", "render_response"}


# ═══════════════════════════════════════════════════════════
# _detect 检测逻辑
# ═══════════════════════════════════════════════════════════

class TestDetect:
    """_detect 各优先级检测"""

    def _make(self):
        from config.plugins.state_check import StateCheckPlugin
        return StateCheckPlugin()

    def test_empty_screen(self):
        p = self._make()
        assert p._detect("", None, False, None) == (None, "empty screen")

    def test_no_match(self):
        p = self._make()
        assert p._detect("hello world", 1, False, None) == (None, "no match")

    def test_alt_screen(self):
        p = self._make()
        assert p._detect("banner\n>>>", 4, True, None) == ("Alt-Screen", "alt screen active")

    def test_shell_prompt_dollar(self):
        p = self._make()
        assert p._detect("banner\n$ ", 2, False, None) == ("WaitingForInput", "shell prompt")

    def test_shell_prompt_hash(self):
        p = self._make()
        assert p._detect("banner\nroot#", 5, False, None) == ("WaitingForInput", "shell prompt")

    def test_shell_prompt_cursor_col0(self):
        """光标在行首（col=0）不触发 shell prompt 检测"""
        p = self._make()
        assert p._detect("banner\n$ ", 0, False, None) == ("Running", "cursor at column 0")

    def test_foreground_shell_process(self):
        p = self._make()
        assert p._detect("some\noutput", 1, False, "bash") == ("WaitingForInput", "shell process (bash)")

    def test_editor_indicator(self):
        p = self._make()
        assert p._detect("some text\n-- insert --", 0, False, None) == ("Editor", "editor mode indicator")

    def test_pager_indicator(self):
        p = self._make()
        assert p._detect("manual page content\n(end)", 0, False, None) == ("Pager", "pager indicator")

    # 注意上面故意写错了几个字用于测试

    def test_password_prompt(self):
        p = self._make()
        assert p._detect("banner\npassword:", 2, False, None) == ("Password", "password prompt")

    def test_confirm_prompt(self):
        p = self._make()
        assert p._detect("banner\nare you sure [y/n]", 3, False, None) == ("Confirm", "confirm prompt")

    def test_error_recent_lines(self):
        p = self._make()
        assert p._detect("line1\nerror: something failed\nline3", 1, False, None) == ("Error", "error indicator")

    def test_error_wins_over_running_at_col0(self):
        """光标在行首（col 0）时错误指示词优先 → Error（不被 Running 遮蔽）"""
        p = self._make()
        assert p._detect("error: something failed", 0, False, None) == ("Error", "error indicator")

    def test_error_recent_lines_cursor_col0(self):
        """错误行在最近 3 行且光标在行首 → Error 优先于 Running"""
        p = self._make()
        assert p._detect("ok line\nwarning: none\nerror: boom", 0, False, None) == ("Error", "error indicator")

    def test_running_no_error_at_col0(self):
        """无错误指示词 + 光标行首 → Running"""
        p = self._make()
        assert p._detect("some\noutput", 0, False, None) == ("Running", "cursor at column 0")

    def test_command_output_line_not_prompt(self):
        """命令输出行（以 $/#/%/> 开头）不应触发 shell prompt 检测"""
        p = self._make()
        assert p._detect("banner\n$ ls -la", 4, False, None) != ("WaitingForInput", "shell prompt")


# ═══════════════════════════════════════════════════════════
# decorate_response（list 装饰）
# ═══════════════════════════════════════════════════════════

class _FakeManager:
    def __init__(self, sessions):
        self._sessions = {s.id: s for s in sessions}

    def get_session(self, sid):
        return self._sessions.get(sid)


class _FakeSession:
    def __init__(self, sid="s1", running=True, mode="pty", text="hello\n$ ", cursor=(2, 1, True),
                 alt=False, pids=(), common_marks=("normal",)):
        self.id = sid
        self._running = running
        self._mode = mode
        self._text = text
        self._cursor = cursor
        self._alt = alt
        self._pids = list(pids)
        self._common_marks = list(common_marks)

    @property
    def running(self):
        return self._running

    @property
    def mode(self):
        return self._mode

    def has_common_mark(self, mark):
        return mark in self._common_marks

    def get_snapshot(self, keep_ansi=False):
        return self._text

    def cursor_position(self):
        return self._cursor

    def is_alt_screen(self):
        return self._alt

    def get_type(self):
        return self._mode


class _FakePluginHost:
    def __init__(self, session):
        self._session = session

    def get_session(self, sid):
        return self._session if self._session.id == sid else None


class TestDecorateList:
    """decorate_response 装饰 list 响应"""

    def test_not_list_returns_none(self):
        from config.plugins.state_check import StateCheckPlugin
        p = StateCheckPlugin()
        resp = {"commandType": "exec"}
        assert p.decorate_response(None, resp) is None

    def test_list_empty_returns_none(self):
        from config.plugins.state_check import StateCheckPlugin
        p = StateCheckPlugin()
        ctx = type("ctx", (), {"manager": _FakeManager([])})()
        assert p.decorate_response(ctx, {"commandType": "list", "sessions": []}) is None

    def test_skips_subagent_sessions(self):
        from config.plugins.state_check import StateCheckPlugin
        p = StateCheckPlugin()
        s = _FakeSession(common_marks=("subagent",))
        ctx = type("ctx", (), {"manager": _FakeManager([s])})()
        resp = {"commandType": "list", "sessions": [{"id": "s1", "running": True}]}
        result = p.decorate_response(ctx, resp)
        assert result is None

    def test_skips_non_pty_sessions(self):
        from config.plugins.state_check import StateCheckPlugin
        p = StateCheckPlugin()
        s = _FakeSession(mode="subprocess")
        ctx = type("ctx", (), {"manager": _FakeManager([s])})()
        resp = {"commandType": "list", "sessions": [{"id": "s1", "running": True}]}
        result = p.decorate_response(ctx, resp)
        assert result is None

    def test_skips_ended_sessions(self):
        from config.plugins.state_check import StateCheckPlugin
        p = StateCheckPlugin()
        s = _FakeSession(running=False)
        ctx = type("ctx", (), {"manager": _FakeManager([s])})()
        resp = {"commandType": "list", "sessions": [{"id": "s1", "running": False}]}
        result = p.decorate_response(ctx, resp)
        assert result is None

    def test_adds_state_check_mark(self):
        from config.plugins.state_check import StateCheckPlugin
        p = StateCheckPlugin()
        s = _FakeSession(common_marks=("normal",))
        ctx = type("ctx", (), {"manager": _FakeManager([s])})()
        resp = {"commandType": "list", "sessions": [{"id": "s1", "running": True}]}
        result = p.decorate_response(ctx, resp)
        assert result is not None
        assert "HEUR" in result["sessions"][0]["status"]


# ═══════════════════════════════════════════════════════════
# render_response（CLI 渲染）
# ═══════════════════════════════════════════════════════════

class TestRenderResponse:
    """render_response 渲染 list"""

    def test_not_list_returns_none(self):
        from config.plugins.state_check import StateCheckPlugin
        p = StateCheckPlugin()
        assert p.render_response(type("ctx", (), {"command": "exec"})(), {"commandType": "exec"}) is None

    def test_renders_list_with_status(self):
        from config.plugins.state_check import StateCheckPlugin
        p = StateCheckPlugin()
        ctx = type("ctx", (), {"command": "list"})()
        resp = {"commandType": "list", "sessions": [{"id": "s1", "status": "running - HEUR:Shell"}]}
        text = p.render_response(ctx, resp)
        assert text is not None
        assert "s1" in text
        assert "HEUR" in text