"""Client 传输层单元测试

测试 Client 类的配置应用、shell 操作符检测、命令构建等。
使用 mock 替代 TCP 连接。
"""

import pytest

from src.client.transport import Client
from src.client.commands import _has_shell_operators, _parse_iso_time


class _FakeCliPlugins:
    """模拟 CliPluginHost：仅 names()/activate()"""

    def __init__(self, names):
        self._names = names
        self.active = []

    def names(self):
        return self._names

    def activate(self, names):
        self.active = list(names)


class TestRoutePlugins:
    """exec --plugin 按 kind 分流：CLI 插件客户端挂钩并记录会话，daemon 插件透传"""

    def test_cli_plugin_hooked_and_recorded(self):
        """CLI 形态插件：客户端 activate + 写入 msg['cliPlugins'] 供会话记录"""
        client = Client(cli_plugins=_FakeCliPlugins(["ai"]))
        msg = {"type": "exec"}
        client._route_plugins(msg, ["ai"])
        assert client._cli_plugins.active == ["ai"]
        assert msg["cliPlugins"] == ["ai"]
        assert "plugins" not in msg

    def test_daemon_plugin_passed_on_exec(self):
        """会话/进程形态插件：exec 时写入 msg['plugins'] 透传 daemon"""
        client = Client(cli_plugins=None)
        msg = {"type": "exec"}
        client._route_plugins(msg, ["files", "state_check"])
        assert msg["plugins"] == ["files", "state_check"]
        assert "cliPlugins" not in msg

    def test_mixed_kinds_routed_independently(self):
        """CLI 与 daemon 插件混用时各自分流"""
        client = Client(cli_plugins=_FakeCliPlugins(["ai"]))
        msg = {"type": "exec"}
        client._route_plugins(msg, ["ai", "files"])
        assert client._cli_plugins.active == ["ai"]
        assert msg["cliPlugins"] == ["ai"]
        assert msg["plugins"] == ["files"]

    def test_no_plugins_noop(self):
        client = Client(cli_plugins=_FakeCliPlugins(["ai"]))
        msg = {"type": "exec"}
        client._route_plugins(msg, None)
        client._route_plugins(msg, [])
        assert client._cli_plugins.active == []
        assert "plugins" not in msg
        assert "cliPlugins" not in msg

    def test_cmd_exec_routes_cli_plugin(self, monkeypatch):
        """cmd_exec --plugin ai：客户端 activate + cliPlugins 记录，不写入 daemon plugins"""
        sent = []
        monkeypatch.setattr(
            "src.client.presenter.print_response",
            lambda r: sent.append(r),
        )
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg, **kwargs: sent.append(msg) or {"type": "result", "session_id": "s"},
        )
        cli = _FakeCliPlugins(["ai"])
        client = Client(cli_plugins=cli)
        client.cmd_exec(
            session_id="s",
            command="echo hi",
            plugins=["ai"],
        )
        assert cli.active == ["ai"]
        exec_msg = next(m for m in sent if isinstance(m, dict) and m.get("type") == "exec")
        assert exec_msg["cliPlugins"] == ["ai"]
        assert "plugins" not in exec_msg


class TestSessionCliPlugins:
    """read/send/mouse 自动挂载会话上的 CLI 插件（无需 --plugin）"""

    def test_session_cli_plugins_intersects(self, monkeypatch):
        """按会话挂载列表 ∩ CLI 插件名取 cli 钩子"""
        cli = _FakeCliPlugins(["ai"])
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg, **kwargs: {
                "type": "result",
                "action": "ls",
                "plugins": [
                    {"name": "ai", "version": ""},
                    {"name": "files", "version": "1.0"},
                ],
            }
            if msg.get("type") == "plugin"
            else {"type": "result"},
        )
        client = Client(cli_plugins=cli)
        assert client._session_cli_plugins("s") == ["ai"]

    def test_session_cli_plugins_error_returns_empty(self, monkeypatch):
        """会话不存在（ls 报错）返回空列表"""
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg, **kwargs: {"type": "error", "message": "no session"},
        )
        client = Client(cli_plugins=_FakeCliPlugins(["ai"]))
        assert client._session_cli_plugins("nope") == []

    def test_cmd_send_activates_session_cli(self, monkeypatch):
        """cmd_send 自动挂载会话上的 CLI 插件，请求不带 --plugin 相关字段"""
        sent = []
        monkeypatch.setattr(
            "src.client.presenter.print_response", lambda r: sent.append(r)
        )

        def fake_send_recv(self, msg, **kwargs):
            if msg.get("type") == "plugin":
                return {"type": "result", "action": "ls",
                        "plugins": [{"name": "ai", "version": ""}]}
            sent.append(msg)
            return {"type": "result", "session_id": "s"}

        monkeypatch.setattr("src.client.transport.Client._send_recv", fake_send_recv)
        cli = _FakeCliPlugins(["ai"])
        client = Client(cli_plugins=cli)
        client.cmd_send(session_id="s", input_text="print(1)")
        assert cli.active == ["ai"]
        send_msg = next(m for m in sent if isinstance(m, dict) and m.get("type") == "send")
        assert "plugins" not in send_msg
        assert "cliPlugins" not in send_msg

    def test_cmd_read_activates_session_cli(self, monkeypatch):
        """cmd_read 自动挂载会话上的 CLI 插件"""
        sent = []
        monkeypatch.setattr(
            "src.client.presenter.print_response", lambda r: sent.append(r)
        )

        def fake_send_recv(self, msg, **kwargs):
            if msg.get("type") == "plugin":
                return {"type": "result", "action": "ls",
                        "plugins": [{"name": "simple", "version": ""}]}
            sent.append(msg)
            return {"type": "result", "session_id": "s"}

        monkeypatch.setattr("src.client.transport.Client._send_recv", fake_send_recv)
        cli = _FakeCliPlugins(["simple"])
        client = Client(cli_plugins=cli)
        client.cmd_read(session_id="s")
        assert cli.active == ["simple"]

    def test_no_cli_plugins_no_query(self, monkeypatch):
        """无 CLI 插件宿主时不发插件查询"""
        called = []
        monkeypatch.setattr(
            "src.client.presenter.print_response", lambda r: called.append(r)
        )

        def fake_send_recv(self, msg, **kwargs):
            called.append(msg.get("type"))
            return {"type": "result", "session_id": "s"}

        monkeypatch.setattr("src.client.transport.Client._send_recv", fake_send_recv)
        client = Client(cli_plugins=None)
        client.cmd_read(session_id="s")
        # 只发 read，不额外发 plugin ls 查询
        assert called.count("plugin") == 0



class TestHasShellOperators:
    """_has_shell_operators 测试"""

    def test_pipe(self):
        assert _has_shell_operators("cat file | grep x") is True

    def test_and(self):
        assert _has_shell_operators("cmd1 && cmd2") is True

    def test_or(self):
        assert _has_shell_operators("cmd1 || cmd2") is True

    def test_semicolon(self):
        tokens = _has_shell_operators("cmd1 ; cmd2")
        assert tokens is True

    def test_redirect_out(self):
        assert _has_shell_operators("echo hi > file") is True

    def test_redirect_append(self):
        assert _has_shell_operators("echo hi >> file") is True

    def test_redirect_in(self):
        assert _has_shell_operators("cmd < file") is True

    def test_background(self):
        assert _has_shell_operators("cmd &") is True

    def test_no_operators(self):
        assert _has_shell_operators("python -c print(1)") is False

    def test_operators_in_quotes(self):
        """引号内的操作符不计"""
        assert _has_shell_operators('echo "a | b"') is False

    def test_empty_string(self):
        assert _has_shell_operators("") is False


class TestParseIsoTime:
    """_parse_iso_time 测试"""

    def test_full_iso(self):
        """完整 ISO 8601"""
        ts = _parse_iso_time("2026-06-07T18:00:00+08:00")
        assert isinstance(ts, float)
        assert ts > 0

    def test_utc_z_suffix(self):
        """Z 后缀"""
        ts = _parse_iso_time("2026-06-07T18:00:00Z")
        assert isinstance(ts, float)

    def test_invalid_raises(self):
        """无效格式抛出 ValueError"""
        with pytest.raises(ValueError):
            _parse_iso_time("not-a-date")


class TestClientApplyConfigDefaults:
    """Client._apply_config_defaults 测试"""

    @pytest.fixture(autouse=True)
    def _isolate_persistent_defaults(self, monkeypatch):
        """隔离本机 ~/.pty-agent/client_defaults.json（set-default 持久化）"""
        monkeypatch.setattr(
            "src.client.config_manager.load_persistent_defaults", lambda: {}
        )

    def test_defaults(self):
        """未传参数时使用配置默认值"""
        client = Client()
        timeout, keep_ansi, encoding, newline, send_eol = client._apply_config_defaults()
        assert timeout == 120.0
        assert keep_ansi is False
        assert encoding is None
        assert newline is False
        assert send_eol == "\r"

    def test_explicit_values(self):
        """显式传参覆盖默认值"""
        client = Client()
        timeout, keep_ansi, encoding, newline, send_eol = client._apply_config_defaults(
            timeout=30, keep_ansi=True, encoding="gbk", newline=True,
        )
        assert timeout == 30
        assert keep_ansi is True
        assert encoding == "gbk"
        assert newline is True

    def test_partial_override(self):
        """部分参数覆盖"""
        client = Client()
        timeout, _, _, _, _ = client._apply_config_defaults(timeout=60)
        assert timeout == 60


class TestClientMaybeSaveEncoding:
    """Client._maybe_save_encoding 测试"""

    def test_save_when_different(self):
        """编码不同时自动保存"""
        client = Client()
        client._maybe_save_encoding("gbk")
        assert client._config.get("encoding") == "gbk"

    def test_no_save_when_same(self):
        """编码相同时不重复保存"""
        client = Client()
        client._config.set("encoding", "utf-8")
        client._maybe_save_encoding("utf-8")
        assert client._config.get("encoding") == "utf-8"


class TestClientShellOperators:
    """Client shell 操作符检测"""

    def test_pty_shell_operator_warning(self, monkeypatch):
        """命令包含 shell 操作符时返回错误"""
        responses = []
        monkeypatch.setattr(
            "src.client.presenter.print_response",
            lambda r: responses.append(r),
        )
        client = Client()
        client.cmd_exec(
            session_id="test",
            command="echo hello | grep x",
        )
        assert len(responses) == 1
        assert responses[0]["type"] == "error"
        assert "shell 操作符" in responses[0]["message"] or "shell operators" in responses[0]["message"]

    def test_pty_force_ignores_warning(self, monkeypatch):
        """--force-pty-mode 忽略 shell 操作符检测"""
        responses = []
        monkeypatch.setattr(
            "src.client.presenter.print_response",
            lambda r: responses.append(r),
        )
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg, **kwargs: {"type": "result", "session_id": "test"},
        )
        client = Client()
        client.cmd_exec(
            session_id="test",
            command='{"data":"echo hello | grep x"}',
            force=True,
        )
        assert len(responses) == 1
        assert responses[0]["type"] == "result"


class TestProcessInput:
    def test_raw_mode_preserves_backslash(self):
        """raw 模式（默认）保留反斜杠"""
        from src.input.text import process_input

        result, _ = process_input("cd C:\\Users")
        assert "C:\\Users" in result
        assert result.endswith("\r")

    def test_json_escaping_mode(self):
        """json_escaping 模式解码转义"""
        from src.input.text import process_input

        result, _ = process_input("line1\\nline2", json_escaping=True)
        assert result == "line1\nline2\r"


class TestClientCmdMouse:
    def test_cmd_mouse_click(self, monkeypatch):
        """cmd_mouse 构造 mouse click 消息"""
        sent = []
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg, **kwargs: sent.append(msg) or {"commandType": "mouse", "performed": True},
        )
        client = Client()
        client.cmd_mouse("test-id", {"action": "click", "coords": {"col": 10, "row": 5}, "button": "left"})
        assert len(sent) == 1
        assert sent[0]["type"] == "mouse"
        assert sent[0]["id"] == "test-id"
        assert sent[0]["action"] == "click"
        assert sent[0]["coords"] == {"col": 10, "row": 5}

    def test_cmd_mouse_drag(self, monkeypatch):
        """cmd_mouse 构造 mouse drag 消息"""
        sent = []
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg, **kwargs: sent.append(msg) or {"commandType": "mouse", "performed": True},
        )
        client = Client()
        client.cmd_mouse("test-id", {
            "action": "drag",
            "coords": {"col": 1, "row": 1},
            "to": {"col": 3, "row": 3},
            "modifiers": ["ctrl"],
        })
        assert sent[0]["action"] == "drag"
        assert sent[0]["to"] == {"col": 3, "row": 3}
        assert sent[0]["modifiers"] == ["ctrl"]

    def test_cmd_mouse_with_output_options(self, monkeypatch):
        """cmd_mouse 传递输出控制参数"""
        sent = []
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg, **kwargs: sent.append(msg) or {"commandType": "mouse", "performed": True},
        )
        client = Client()
        client.cmd_mouse(
            "test-id", {"action": "click", "coords": {"col": 1, "row": 1}},
            trigger=">>>", timeout=5, snapshot_diff=True,
            output_path="out.svg", response_format="svg",
        )
        assert sent[0]["trigger"] == ">>>"
        assert sent[0]["timeout"] == 5
        assert sent[0]["snapshot_diff"] is True
        assert sent[0]["include_screen_buffer"] is True