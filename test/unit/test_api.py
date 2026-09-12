"""PtyClient 门面单元测试

测试 PtyClient 的配置应用、shell 操作符检测、--pty/--shell 冲突、
命令消息构建等。使用 mock 替代共享内存通信。
所有 cmd_* 方法返回结构化 dict（不打印），呈现由 presenters 负责。
"""

from unittest.mock import patch

from src.client.api import PtyClient, _has_shell_operators


class TestHasShellOperators:
    """_has_shell_operators 测试"""

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


class TestApiConfigDefaults:
    """PtyClient._apply_config_defaults 测试"""

    def test_defaults(self):
        client = PtyClient()
        timeout, newline = client._apply_config_defaults()
        assert timeout == 120.0
        assert newline is False

    def test_explicit_values(self):
        client = PtyClient()
        timeout, newline = client._apply_config_defaults(
            timeout=30, newline=True,
        )
        assert timeout == 30
        assert newline is True

    def test_partial_override(self):
        client = PtyClient()
        timeout, _ = client._apply_config_defaults(timeout=60)
        assert timeout == 60


class TestApiPtyConflict:
    """PtyClient --pty 与 --shell 冲突检测（返回 dict 而非打印）"""

    def test_pty_and_shell_conflict(self):
        resp = PtyClient().cmd_exec(
            session_id="test", command="echo hello", pty=True,
            shell="powershell",
        )
        assert resp["type"] == "error"
        assert "不能同时使用" in resp["error"]

    def test_pty_shell_operator_error(self):
        resp = PtyClient().cmd_exec(
            session_id="test", command="echo hello | grep x", pty=True,
        )
        assert resp["type"] == "error"
        assert "shell 操作符" in resp["error"]

    def test_pty_force_splits_command(self):
        """--force-pty-mode 忽略 shell 操作符检测，命令被拆分"""
        client = PtyClient()
        with patch.object(client, "_send", return_value={"type": "result"}):
            resp = client.cmd_exec(
                session_id="test",
                command='python -c "print(1)"',
                pty=True, force=True,
            )
        assert resp["type"] == "result"

    def test_normal_exec_builds_command(self):
        client = PtyClient()
        captured = {}

        def fake_send(msg):
            captured.update(msg)
            return {"type": "result"}

        with patch.object(client, "_send", side_effect=fake_send):
            client.cmd_exec(
                session_id="s1", command="python -u -i",
                trigger=">>>", timeout=30,
            )
        assert captured["type"] == "exec"
        assert captured["id"] == "s1"
        assert captured["command"] == "python -u -i"
        assert captured["trigger"] == ">>>"
        assert captured["timeout"] == 30
        assert captured["pty"] is False


class TestApiRead:
    """read 语义：--offset 已删除"""

    def test_read_builds_msg(self):
        client = PtyClient()
        captured = {}

        def fake_send(msg):
            captured.update(msg)
            return {"type": "result"}

        with patch.object(client, "_send", side_effect=fake_send):
            client.cmd_read("s1", lines="5", grep="Error", full=True)
        assert captured == {
            "type": "read", "id": "s1", "full": True,
            "lines": "5", "grep": "Error",
        }


class TestProcessInput:
    """process_input 测试"""

    def test_raw_mode_preserves_backslash(self):
        from src.client.input import process_input
        result = process_input("cd C:\\Users")
        assert "C:\\Users" in result
        assert result.endswith("\n")
