"""Client 传输层单元测试

测试 Client 类的配置应用、shell 操作符检测、命令构建等。
使用 mock 替代 TCP 连接。
"""

import pytest

from src.client.transport import Client, _has_shell_operators, _parse_iso_time


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

    def test_defaults(self):
        """未传参数时使用配置默认值"""
        client = Client()
        timeout, keep_ansi, encoding, newline = client._apply_config_defaults()
        assert timeout == 120.0
        assert keep_ansi is False
        assert encoding is None
        assert newline is False

    def test_explicit_values(self):
        """显式传参覆盖默认值"""
        client = Client()
        timeout, keep_ansi, encoding, newline = client._apply_config_defaults(
            timeout=30, keep_ansi=True, encoding="gbk", newline=True,
        )
        assert timeout == 30
        assert keep_ansi is True
        assert encoding == "gbk"
        assert newline is True

    def test_partial_override(self):
        """部分参数覆盖"""
        client = Client()
        timeout, _, _, _ = client._apply_config_defaults(timeout=60)
        assert timeout == 60


class TestClientMaybeSaveEncoding:
    """Client._maybe_save_encoding 测试"""

    def test_save_when_different(self):
        """编码不同时自动保存"""
        client = Client()
        client._maybe_save_encoding("gbk", False)
        assert client._config.get("encoding") == "gbk"

    def test_no_save_when_same(self):
        """编码相同时不重复保存"""
        client = Client()
        client._config.set("encoding", "utf-8")
        client._maybe_save_encoding("utf-8", False)
        assert client._config.get("encoding") == "utf-8"

    def test_force_save(self):
        """save_encoding=True 强制保存"""
        client = Client()
        client._config.set("encoding", "utf-8")
        client._maybe_save_encoding("utf-8", True)
        assert client._config.get("encoding") == "utf-8"


class TestClientPtyConflict:
    """Client --pty 与 --shell 冲突检测"""

    def test_pty_and_shell_conflict(self, monkeypatch):
        """同时指定 pty 和 shell 时返回错误"""
        responses = []
        monkeypatch.setattr(
            "src.client.transport.print_response",
            lambda r: responses.append(r),
        )
        client = Client()
        client.cmd_exec(
            session_id="test",
            command='{"data":"echo hello"}',
            pty=True,
            shell="powershell",
        )
        assert len(responses) == 1
        assert responses[0]["type"] == "error"
        assert "不能同时使用" in responses[0]["error"]

    def test_pty_shell_operator_warning(self, monkeypatch):
        """--pty 模式下 shell 操作符警告"""
        responses = []
        monkeypatch.setattr(
            "src.client.transport.print_response",
            lambda r: responses.append(r),
        )
        client = Client()
        client.cmd_exec(
            session_id="test",
            command="echo hello | grep x",
            pty=True,
        )
        assert len(responses) == 1
        assert responses[0]["type"] == "error"
        assert "shell 操作符" in responses[0]["error"]

    def test_pty_force_ignores_warning(self, monkeypatch):
        """--force-pty-mode 忽略 shell 操作符检测"""
        responses = []
        monkeypatch.setattr(
            "src.client.transport.print_response",
            lambda r: responses.append(r),
        )
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg: {"type": "result", "session_id": "test"},
        )
        client = Client()
        client.cmd_exec(
            session_id="test",
            command='{"data":"echo hello | grep x"}',
            pty=True,
            force=True,
        )
        assert len(responses) == 1
        assert responses[0]["type"] == "result"


class TestProcessInput:
    """process_input 测试"""

    def test_raw_mode_preserves_backslash(self):
        """raw 模式（默认）保留反斜杠"""
        from src.client.input import process_input

        result = process_input("cd C:\\Users")
        assert "C:\\Users" in result
        assert result.endswith("\n")

    def test_json_escaping_mode(self):
        """json_escaping 模式解码转义"""
        from src.client.input import process_input

        result = process_input("line1\\nline2", json_escaping=True)
        assert result == "line1\nline2\n"