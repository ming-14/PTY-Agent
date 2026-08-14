"""config/plugins/state_check 插件单测 — 启发式状态检测（返回钩子 + 命令钩子）"""

import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)

from src.plugins.base import Plugin  # noqa: E402
from src.plugins.loader import load_module, extract_plugin_class, validate_plugin  # noqa: E402
from src.plugins.host import PluginHost  # noqa: E402

_PLUGIN_PATH = os.path.join(_PROJECT_ROOT, "config", "plugins", "state_check")


@pytest.fixture(scope="module")
def plugin_cls():
    assert os.path.exists(_PLUGIN_PATH), "state_check 目录不在 config/plugins/ 中"
    cls = extract_plugin_class(load_module(_PLUGIN_PATH), _PLUGIN_PATH)
    assert cls is not None
    assert validate_plugin(cls)
    return cls


@pytest.fixture
def detector(plugin_cls):
    return plugin_cls()


class FakeSession:
    def __init__(self, text="", cursor=(None, None, None), alt=False, pids=()):
        self.id = "sc"
        self._text = text
        self._cursor = cursor
        self._alt = alt
        self._pids = list(pids)

    def get_snapshot(self):
        return self._text

    def cursor_position(self):
        return self._cursor

    def is_alt_screen(self):
        return self._alt

    def get_pty_process_list(self):
        return self._pids


def _detect(p, screen, cursor_x, alt=False, process=None):
    """调用插件内部检测链（不经过 ctx）"""
    return p._detect(screen, cursor_x, alt, process)


class TestPriorityChain:
    def test_alt_screen_editor(self, detector):
        state, _ = _detect(detector, "some text", 3, alt=True)
        assert state == "Editor"

    def test_repl_prompt(self, detector):
        state, _ = _detect(detector, "banner\n>>> ", 4)
        assert state == "Repl"

    def test_repl_variants(self, detector):
        for prompt in (">>> ", "... ", "In [1]: ", "(Pdb) ", "mysql> ", "node> ",
                       "psql> ", "julia> ", "ghci> "):
            state, _ = _detect(detector, prompt, 4)
            assert state == "Repl", prompt

    def test_repl_cursor_at_col0_falls_through(self, detector):
        # cursor_x == 0 不判 Repl，落到 Running 级
        state, _ = _detect(detector, ">>> ", 0)
        assert state == "Running"

    def test_shell_prompt(self, detector):
        state, _ = _detect(detector, "whoami\nC:\\Users\\me> ", 4)
        assert state == "WaitingForInput"

    def test_shell_prompt_dollar(self, detector):
        state, _ = _detect(detector, "ls\n$ ", 4)
        assert state == "WaitingForInput"

    def test_shell_prompt_cursor_col0_falls_to_running(self, detector):
        state, _ = _detect(detector, "$ ", 0)
        assert state == "Running"

    def test_foreground_shell_process(self, detector):
        state, _ = _detect(detector, "loading...", 5, process="pwsh.exe")
        assert state == "WaitingForInput"

    def test_editor_indicator(self, detector):
        state, _ = _detect(detector, "buffer.txt\n-- INSERT --", 6)
        assert state == "Editor"

    def test_pager_indicator(self, detector):
        state, _ = _detect(detector, "man ls\n(END)", 2)
        assert state == "Pager"

    def test_agent_permission_whole_screen(self, detector):
        state, _ = _detect(detector, "buf: do you want to continue\n...\nyes or no", 5)
        assert state == "Confirm"

    def test_password_recent_lines(self, detector):
        state, _ = _detect(detector, "enter\n[sudo] password for rikka:", 8)
        assert state == "Password"

    def test_password_not_suppress_confirm_when_not_last_line(self, detector):
        # 密码提示在倒数第二行（已输入），当前最后一行是确认提示 → Confirm
        # 修复前：Password 看最近 3 行会命中密码行而压制 Confirm
        state, _ = _detect(detector, "[sudo] password for rikka:\nOverwrite? [y/n]", 6)
        assert state == "Confirm"

    def test_password_rolled_out_repl_takes_over(self, detector):
        # 密码行在倒数第二行，最后一行是 REPL 提示符 → Repl（不被 Password 压制）
        state, _ = _detect(detector, "[sudo] password for x:\n>>> ", 4)
        assert state == "Repl"

    def test_confirm_recent_lines(self, detector):
        state, _ = _detect(detector, "The file exists.\nOverwrite? [y/n]", 6)
        assert state == "Confirm"

    def test_running_cursor_col0(self, detector):
        state, _ = _detect(detector, "compiling...\n", 0)
        assert state == "Running"

    def test_error_recent_lines(self, detector):
        state, _ = _detect(detector, "make\nTraceback (most recent call last):\nValueError: bad", 5)
        assert state == "Error"

    def test_error_priority_below_running(self, detector):
        # 错误指示词但光标在行首 → Running
        state, _ = _detect(detector, "error: boom", 0)
        assert state == "Running"

    def test_empty_screen(self, detector):
        state, reason = _detect(detector, "", 0)
        assert state is None

    def test_no_match(self, detector):
        state, _ = _detect(detector, "compiling", 5)
        assert state is None

    # ── 防误匹配：命令输出行（$ / # / > / % 开头）不作为提示符判定 ──

    def test_command_output_line_not_prompt(self, detector):
        # 命令回显行以 "$ " 开头（如 $ ls）→ 不判 WaitingForInput
        state, _ = _detect(detector, "config\n$ ls -la", 4)
        assert state not in ("WaitingForInput", "Repl")

    def test_command_output_line_dollar_echo(self, detector):
        # $ echo ">>>" 的整行输出以 "$ " 开头 → 不判 Repl / WaitingForInput
        state, _ = _detect(detector, "$ echo \">>> \"", 2)
        assert state not in ("Repl", "WaitingForInput")

    def test_command_output_line_hash(self, detector):
        # 输出行以 "# " 开头（如注释回显）→ 不判提示符
        state, _ = _detect(detector, "config\n# comment here", 4)
        assert state not in ("WaitingForInput", "Repl")

    def test_normal_shell_prompt_still_detected(self, detector):
        # 常规提示符（非以 $ / # / > / % 开头）不受防误匹配影响
        state, _ = _detect(detector, "whoami\nuser@host:~/code$ ", 4)
        assert state == "WaitingForInput"

    def test_bare_dollar_prompt_still_detected(self, detector):
        # 纯 "$ " 提示符（rstrip 后为 "$"）不命中命令输出前缀，正常判定
        state, _ = _detect(detector, "ls\n$ ", 4)
        assert state == "WaitingForInput"

    def test_repl_unaffected(self, detector):
        # ">>> " 不以命令输出前缀开头，仍判 Repl
        state, _ = _detect(detector, "banner\n>>> ", 4)
        assert state == "Repl"


class TestReturnHook:
    """返回钩子：命令返回时触发一次并携带状态"""

    def test_inspect_state_dict(self, detector):
        session = FakeSession(text="banner\n>>> ", cursor=(4, 2, True))
        host = PluginHost(session, [detector])
        result = host.inspect_state()
        assert result == {"state": "Repl", "reason": "repl prompt", "altScreen": False}

    def test_inspect_state_alt_screen(self, detector):
        session = FakeSession(text="vim screen", cursor=(5, 3, True), alt=True)
        host = PluginHost(session, [detector])
        result = host.inspect_state()
        assert result["state"] == "Editor"
        assert result["altScreen"] is True

    def test_inspect_state_uses_live_session(self, detector):
        # 返回钩子读会话实时状态，不依赖任何缓存
        session = FakeSession(text="", cursor=(None, None, None))
        host = PluginHost(session, [detector])
        result = host.inspect_state()
        assert result["state"] is None

    def test_empty_chain_returns_none(self):
        host = PluginHost(FakeSession(), [])
        assert host.inspect_state() is None


class TestCommandHook:
    """命令钩子：plugin cmd 查询状态"""

    def test_status_command(self, detector):
        session = FakeSession(text="banner\n>>> ", cursor=(4, 2, True))
        host = PluginHost(session, [detector])
        result = host.handle_command("state_check", {"command": "status"})
        assert result["state"] == "Repl"
        assert result["altScreen"] is False

    def test_unknown_command(self, detector):
        host = PluginHost(FakeSession(), [detector])
        assert host.handle_command("state_check", {"command": "nope"}) is None


class TestPluginContract:
    def test_no_trigger_declaration(self, plugin_cls):
        # 纯钩子插件：无事件/轮询触发声明
        assert plugin_cls.triggers == []

    def test_two_hooks_only(self, plugin_cls):
        # 插件仅实现返回钩子与命令钩子
        assert plugin_cls.inspect_state is not Plugin.inspect_state
        assert plugin_cls.handle_command is not Plugin.handle_command