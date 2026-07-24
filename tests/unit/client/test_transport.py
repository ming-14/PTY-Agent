"""client/transport.py 单元测试

测试 Client 类的配置应用、shell 操作符检测、snapshot-mode 逻辑、
连接路由（明文/TLS 分流）等。
"""

import pytest
from unittest.mock import patch, MagicMock

from src.client.transport import Client, _has_shell_operators, _parse_iso_time


class TestHasShellOperators:
    def test_pipe(self):
        assert _has_shell_operators("cat file | grep x") is True

    def test_and(self):
        assert _has_shell_operators("cmd1 && cmd2") is True

    def test_or(self):
        assert _has_shell_operators("cmd1 || cmd2") is True

    def test_semicolon(self):
        assert _has_shell_operators("cmd1 ; cmd2") is True

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
        assert _has_shell_operators('echo "a | b"') is False

    def test_empty_string(self):
        assert _has_shell_operators("") is False


class TestParseIsoTime:
    def test_full_iso(self):
        ts = _parse_iso_time("2026-06-07T18:00:00+08:00")
        assert isinstance(ts, float)
        assert ts > 0

    def test_utc_z_suffix(self):
        ts = _parse_iso_time("2026-06-07T18:00:00Z")
        assert isinstance(ts, float)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_iso_time("not-a-date")


class TestClientApplyConfigDefaults:
    def test_defaults(self):
        client = Client()
        timeout, keep_ansi, encoding, newline, send_eol = client._apply_config_defaults()
        assert timeout == 120.0
        assert keep_ansi is False
        assert encoding is None
        assert newline is False

    def test_explicit_values(self):
        client = Client()
        timeout, keep_ansi, encoding, newline, send_eol = client._apply_config_defaults(
            timeout=30, keep_ansi=True, encoding="gbk", newline=True,
        )
        assert timeout == 30
        assert keep_ansi is True
        assert encoding == "gbk"
        assert newline is True

    def test_partial_override(self):
        client = Client()
        timeout, _, _, _, _ = client._apply_config_defaults(timeout=60)
        assert timeout == 60


class TestClientShellOperators:
    def test_pty_shell_operator_warning(self, monkeypatch):
        responses = []
        monkeypatch.setattr(
            "src.client.transport.print_response",
            lambda r: responses.append(r),
        )
        client = Client()
        client.cmd_exec(session_id="test", command="echo hello | grep x")
        assert len(responses) == 1
        assert responses[0]["type"] == "error"

    def test_pty_force_ignores_warning(self, monkeypatch):
        responses = []
        monkeypatch.setattr(
            "src.client.transport.print_response",
            lambda r: responses.append(r),
        )
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg: {"type": "result", "sessionId": "test"},
        )
        client = Client()
        client.cmd_exec(session_id="test", command="echo hello | grep x", force=True)
        assert len(responses) == 1
        assert responses[0]["type"] == "result"

    def test_always_return_snapshot_enables_snapshot_mode(self, monkeypatch):
        sent_msgs = []
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg: (sent_msgs.append(msg), {"type": "result", "sessionId": "test"})[1],
        )
        monkeypatch.setattr("src.client.transport.print_response", lambda r: None)
        client = Client()
        client._config.set("always_return_snapshot", "on")
        client.cmd_exec(session_id="test", command=["echo", "hello"])
        assert sent_msgs[-1].get("snapshot_mode") is True


class TestClientMaybeSaveEncoding:
    def test_save_when_different(self):
        client = Client()
        client._maybe_save_encoding("gbk")
        assert client._config.get("encoding") == "gbk"

    def test_no_save_when_same(self):
        client = Client()
        client._config.set("encoding", "utf-8")
        client._maybe_save_encoding("utf-8")
        assert client._config.get("encoding") == "utf-8"


class TestClientCmdSend:
    def test_send_with_snapshot(self, monkeypatch):
        sent_msgs = []
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg: (sent_msgs.append(msg), {"type": "result", "sessionId": "test"})[1],
        )
        monkeypatch.setattr("src.client.transport.print_response", lambda r: None)
        client = Client()
        client.cmd_send(session_id="test", input_text="hello", snapshot=True)
        assert sent_msgs[-1].get("snapshot") is True

    def test_send_eol_resolution(self, monkeypatch):
        sent_msgs = []
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg: (sent_msgs.append(msg), {"type": "result", "sessionId": "test"})[1],
        )
        monkeypatch.setattr("src.client.transport.print_response", lambda r: None)
        client = Client()
        client.cmd_send(session_id="test", input_text="hello", send_eol="cr")
        assert sent_msgs[-1]["input"].endswith("\r")


class TestClientCmdRead:
    def test_read_with_snapshot(self, monkeypatch):
        sent_msgs = []
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg: (sent_msgs.append(msg), {"type": "result", "sessionId": "test"})[1],
        )
        monkeypatch.setattr("src.client.transport.print_response", lambda r: None)
        client = Client()
        client.cmd_read(session_id="test", snapshot=True)
        assert sent_msgs[-1].get("snapshot") is True

    def test_read_with_lines(self, monkeypatch):
        sent_msgs = []
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg: (sent_msgs.append(msg), {"type": "result", "sessionId": "test"})[1],
        )
        monkeypatch.setattr("src.client.transport.print_response", lambda r: None)
        client = Client()
        client.cmd_read(session_id="test", lines="10")
        assert sent_msgs[-1].get("lines") == "10"

    def test_read_with_grep(self, monkeypatch):
        sent_msgs = []
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg: (sent_msgs.append(msg), {"type": "result", "sessionId": "test"})[1],
        )
        monkeypatch.setattr("src.client.transport.print_response", lambda r: None)
        client = Client()
        client.cmd_read(session_id="test", grep="error")
        assert sent_msgs[-1].get("grep") == "error"


class TestClientCmdExecEnv:
    def test_invalid_env_format(self, monkeypatch):
        responses = []
        monkeypatch.setattr(
            "src.client.transport.print_response",
            lambda r: responses.append(r),
        )
        client = Client()
        client.cmd_exec(session_id="test", command="echo", env=["NOEQUALSSIGN"])
        assert len(responses) == 1
        assert responses[0]["type"] == "error"
        assert "Invalid --env" in responses[0]["error"]

    def test_valid_env(self, monkeypatch):
        sent_msgs = []
        monkeypatch.setattr(
            "src.client.transport.Client._send_recv",
            lambda self, msg: (sent_msgs.append(msg), {"type": "result", "sessionId": "test"})[1],
        )
        monkeypatch.setattr("src.client.transport.print_response", lambda r: None)
        client = Client()
        client.cmd_exec(session_id="test", command="echo", env=["KEY=VALUE"])
        assert sent_msgs[-1].get("env") == {"KEY": "VALUE"}


class TestClientConnectRouting:
    """_connect() 明文/TLS 路由测试（Phase 5）

    路由规则：
    - pubkey + remote（DAEMON_REMOTE_HOST 非空或 CLI --host）→ _connect_tls
    - 其他（token/none 或同机 pubkey）→ _connect_plain
    """

    def test_pubkey_remote_routes_to_tls(self, monkeypatch):
        """CLIENT_AUTH_METHOD=pubkey + DAEMON_REMOTE_HOST 非空 → _connect_tls"""
        monkeypatch.setattr("src.client.transport.CLIENT_AUTH_METHOD", "pubkey")
        monkeypatch.setattr("src.client.transport.DAEMON_REMOTE_HOST", "192.168.1.100")

        client = Client()
        mock_tls = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(client, "_connect_tls", mock_tls)

        client._connect()

        mock_tls.assert_called_once()

    def test_token_routes_to_plain(self, monkeypatch):
        """CLIENT_AUTH_METHOD=token → _connect_plain"""
        monkeypatch.setattr("src.client.transport.CLIENT_AUTH_METHOD", "token")
        monkeypatch.setattr("src.client.transport.DAEMON_REMOTE_HOST", "")

        client = Client()
        mock_plain = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(client, "_connect_plain", mock_plain)

        client._connect(autostart=False)

        mock_plain.assert_called_once_with(False)

    def test_pubkey_no_remote_routes_to_plain(self, monkeypatch):
        """CLIENT_AUTH_METHOD=pubkey + DAEMON_REMOTE_HOST 空 → _connect_plain（同机）"""
        monkeypatch.setattr("src.client.transport.CLIENT_AUTH_METHOD", "pubkey")
        monkeypatch.setattr("src.client.transport.DAEMON_REMOTE_HOST", "")

        client = Client()
        mock_plain = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(client, "_connect_plain", mock_plain)

        client._connect()

        mock_plain.assert_called_once()

    def test_cli_host_override_routes_to_tls(self, monkeypatch):
        """CLI --host 覆盖 → 即使配置无 DAEMON_REMOTE_HOST 也走 TLS"""
        monkeypatch.setattr("src.client.transport.CLIENT_AUTH_METHOD", "pubkey")
        monkeypatch.setattr("src.client.transport.DAEMON_REMOTE_HOST", "")

        client = Client(host="10.0.0.5", port=9999)
        mock_tls = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(client, "_connect_tls", mock_tls)

        client._connect()

        mock_tls.assert_called_once()

    def test_none_auth_routes_to_plain(self, monkeypatch):
        """CLIENT_AUTH_METHOD=none → _connect_plain"""
        monkeypatch.setattr("src.client.transport.CLIENT_AUTH_METHOD", "none")
        monkeypatch.setattr("src.client.transport.DAEMON_REMOTE_HOST", "192.168.1.100")

        client = Client()
        mock_plain = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(client, "_connect_plain", mock_plain)

        client._connect()

        mock_plain.assert_called_once()
