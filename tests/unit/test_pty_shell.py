"""SubprocessPseudoTerminal 解释器选择测试

验证 --shell 参数能否正确切换命令解释器（cmd/powershell/pwsh/bash）。
"""

import sys
import pytest

from src.pty.subprocess import SubprocessPseudoTerminal, PseudoTerminal


class TestSubprocessShellSelection:
    """SubprocessPseudoTerminal shell 选择测试"""

    def test_shell_map_contains_expected_keys(self):
        """_SHELL_MAP 包含所有预期的解释器"""
        if sys.platform != "win32":
            pytest.skip("Windows only")
        from src.pty.windows.subprocess_win import _SHELL_MAP
        assert "cmd" in _SHELL_MAP
        assert "powershell" in _SHELL_MAP
        assert "pwsh" in _SHELL_MAP

    def test_shell_map_cmd_is_none(self):
        """cmd 映射为 None → 使用 shell=True"""
        if sys.platform != "win32":
            pytest.skip("Windows only")
        from src.pty.windows.subprocess_win import _SHELL_MAP
        assert _SHELL_MAP["cmd"] is None

    def test_shell_map_powershell_is_string(self):
        """powershell 映射为字符串 'powershell.exe'"""
        if sys.platform != "win32":
            pytest.skip("Windows only")
        from src.pty.windows.subprocess_win import _SHELL_MAP
        assert _SHELL_MAP["powershell"] == "powershell.exe"

    def test_shell_map_pwsh_is_string(self):
        """pwsh 映射为字符串 'pwsh'"""
        if sys.platform != "win32":
            pytest.skip("Windows only")
        from src.pty.windows.subprocess_win import _SHELL_MAP
        assert _SHELL_MAP["pwsh"] == "pwsh"

    def test_shell_map_bash_is_list(self):
        """bash 不在默认 _SHELL_MAP 中（已移除）"""
        if sys.platform != "win32":
            pytest.skip("Windows only")
        from src.pty.windows.subprocess_win import _SHELL_MAP
        assert "bash" not in _SHELL_MAP

    def test_default_shell_is_none(self):
        """shell 参数默认为 None → 列表命令 shell=False"""
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        try:
            pty._proc.wait(timeout=5)
            assert pty._proc.args == [sys.executable, "-c", "import sys; sys.exit(0)"]
        finally:
            pty.close()

    def test_shell_cmd_on_string_command(self):
        """shell='cmd' 且命令为字符串 → 使用 cmd.exe /c chcp ... && command"""
        pty = SubprocessPseudoTerminal(
            "echo hello", cols=80, rows=24, shell="cmd",
        )
        try:
            pty._proc.wait(timeout=5)
            args = pty._proc.args
            assert isinstance(args, list)
            assert args[0].lower() == "cmd.exe"
            assert "/c" in args
        finally:
            pty.close()

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="非 cmd 的 shell 映射仅 Windows 生效",
    )
    def test_shell_powershell_uses_encoded_command(self):
        """shell='powershell' 时使用 -EncodedCommand"""
        import shutil
        if not shutil.which("powershell"):
            pytest.skip("powershell.exe 不在 PATH 中")
        pty = SubprocessPseudoTerminal(
            "echo hello", cols=80, rows=24, shell="powershell",
        )
        try:
            args = pty._proc.args
            assert isinstance(args, list)
            assert "powershell" in args[0].lower()
            assert "-EncodedCommand" in args
        finally:
            pty.close()

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="非 cmd 的 shell 映射仅 Windows 生效",
    )
    def test_shell_pwsh_uses_encoded_command(self):
        """shell='pwsh' 时使用 -EncodedCommand"""
        import shutil
        if not shutil.which("pwsh"):
            pytest.skip("pwsh.exe 不在 PATH 中")
        pty = SubprocessPseudoTerminal(
            "echo hello", cols=80, rows=24, shell="pwsh",
        )
        try:
            pty._proc.wait(timeout=5)
            args = pty._proc.args
            assert isinstance(args, list)
            assert "pwsh" in args[0].lower()
            assert "-EncodedCommand" in args
        finally:
            pty.close()

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="非 cmd 的 shell 映射仅 Windows 生效",
    )
    def test_shell_bash_constructs_list(self):
        """shell='bash' 时回退到 cmd.exe（bash 不在 _SHELL_MAP）"""
        import shutil
        pty = SubprocessPseudoTerminal(
            "echo hello", cols=80, rows=24, shell="bash",
        )
        try:
            pty._proc.wait(timeout=5)
            args = pty._proc.args
            assert isinstance(args, list)
            assert "cmd.exe" in args[0].lower()
        finally:
            pty.close()

    def test_unknown_shell_falls_back_to_cmd(self):
        """不认识的 shell 值回退到 cmd.exe"""
        pty = SubprocessPseudoTerminal(
            "echo hello", cols=80, rows=24, shell="unknown_shell_name",
        )
        try:
            pty._proc.wait(timeout=5)
            args = pty._proc.args
            assert isinstance(args, list)
            assert args[0].lower() == "cmd.exe"
        finally:
            pty.close()

    def test_shell_with_list_command_noop(self):
        """列表命令下 shell 参数被忽略"""
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "print('test')"],
            shell="powershell",
        )
        try:
            pty._proc.wait(timeout=5)
            assert pty._proc.args == [sys.executable, "-c", "print('test')"]
        finally:
            pty.close()

    def test_exit_code_with_shell_cmd(self):
        """shell='cmd' 时退出码仍正确"""
        pty = SubprocessPseudoTerminal(
            "exit 42", cols=80, rows=24, shell="cmd",
        )
        try:
            pty._proc.wait(timeout=5)
            assert pty.get_exit_code() == 42
        finally:
            pty.close()


class TestShellConflict:
    """--pty 与 --shell 冲突检测测试"""

    def test_pty_and_shell_conflict_detected(self, monkeypatch):
        """同时指定 --pty 和 --shell 时返回错误"""
        from src.client.transport import Client

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
        assert "mutually exclusive" in responses[0]["error"]

    def test_pty_without_shell_ok(self, monkeypatch):
        """--pty 不带 --shell 时不触发冲突"""
        from src.client.transport import Client

        responses = []
        monkeypatch.setattr(
            "src.client.transport.print_response",
            lambda r: responses.append(r),
        )
        monkeypatch.setattr(
            "src.client.transport.Client._connect",
            lambda self, **kw: (_ for _ in ()).throw(Exception("mock")),
        )

        client = Client()
        with pytest.raises(Exception, match="mock"):
            client.cmd_exec(
                session_id="test",
                command='{"data":"echo hello"}',
                pty=True,
                shell=None,
            )
        assert len(responses) == 0

    def test_shell_without_pty_ok(self, monkeypatch):
        """--shell 不带 --pty 时不触发冲突"""
        from src.client.transport import Client

        responses = []
        monkeypatch.setattr(
            "src.client.transport.print_response",
            lambda r: responses.append(r),
        )
        monkeypatch.setattr(
            "src.client.transport.Client._connect",
            lambda self, **kw: (_ for _ in ()).throw(Exception("mock")),
        )

        client = Client()
        with pytest.raises(Exception, match="mock"):
            client.cmd_exec(
                session_id="test",
                command='{"data":"echo hello"}',
                pty=False,
                shell="pwsh",
            )
        assert len(responses) == 0
