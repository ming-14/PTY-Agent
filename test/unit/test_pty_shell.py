"""SubprocessBackend 解释器选择测试

验证 --shell 参数能否正确切换命令解释器（cmd/powershell/pwsh/bash）。
"""

import sys
import pytest

from src.backend.subprocess import SubprocessBackend


class TestSubprocessShellSelection:
    """SubprocessBackend shell 选择测试

    验证 _SHELL_MAP 映射、shell=None 默认行为、指定 shell 的 Popen 构建。
    """

    def test_shell_map_contains_expected_keys(self):
        """_SHELL_MAP 包含所有预期的解释器"""
        assert "cmd" in SubprocessBackend._SHELL_MAP
        assert "powershell" in SubprocessBackend._SHELL_MAP
        assert "pwsh" in SubprocessBackend._SHELL_MAP
        assert "bash" in SubprocessBackend._SHELL_MAP

    def test_shell_map_cmd_is_none(self):
        """cmd 映射为 None → 使用 shell=True"""
        assert SubprocessBackend._SHELL_MAP["cmd"] is None

    def test_shell_map_powershell_format(self):
        """powershell 映射为 [powershell.exe, -Command]"""
        spec = SubprocessBackend._SHELL_MAP["powershell"]
        assert isinstance(spec, list)
        assert len(spec) == 2
        assert "powershell" in spec[0].lower()
        assert spec[1] == "-Command"

    def test_shell_map_pwsh_format(self):
        """pwsh 映射为 [pwsh.exe, -Command]"""
        spec = SubprocessBackend._SHELL_MAP["pwsh"]
        assert isinstance(spec, list)
        assert len(spec) == 2
        assert "pwsh" in spec[0].lower()
        assert spec[1] == "-Command"

    def test_shell_map_bash_format(self):
        """bash 映射为 [bash.exe, -c]"""
        spec = SubprocessBackend._SHELL_MAP["bash"]
        assert isinstance(spec, list)
        assert len(spec) == 2
        assert "bash" in spec[0].lower()
        assert spec[1] == "-c"

    def test_default_shell_is_none(self):
        """shell 参数默认为 None → 使用 cmd.exe（shell=True）"""
        pty = SubprocessBackend(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        try:
            pty._proc.wait(timeout=5)
            # shell=None 时内部 use_shell 取决于 command 类型
            # 列表命令 → shell=False
            assert pty._proc.args == [sys.executable, "-c", "import sys; sys.exit(0)"]
        finally:
            pty.close()

    def test_shell_cmd_on_string_command(self):
        """shell='cmd' 且命令为字符串 → 使用 shell=True"""
        pty = SubprocessBackend(
            "echo hello", cols=80, rows=24, shell="cmd",
        )
        try:
            pty._proc.wait(timeout=5)
            # shell=True 时 command 保持字符串，不走列表
            assert pty._proc.args == "echo hello"
        finally:
            pty.close()

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="非 cmd 的 shell 映射仅 Windows 生效",
    )
    def test_shell_powershell_constructs_list(self):
        """shell='powershell' 时构建 [powershell.exe, -Command, command] 列表"""
        import shutil
        if not shutil.which("powershell"):
            pytest.skip("powershell.exe 不在 PATH 中")
        pty = SubprocessBackend(
            "echo hello", cols=80, rows=24, shell="powershell",
        )
        try:
            args = pty._proc.args
            assert isinstance(args, list)
            assert len(args) == 3
            assert "powershell" in args[0].lower()
            assert args[1] == "-Command"
            assert args[2] == "echo hello"
        finally:
            pty.close()

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="非 cmd 的 shell 映射仅 Windows 生效",
    )
    def test_shell_pwsh_constructs_list(self):
        """shell='pwsh' 时构建 [pwsh.exe, -Command, command] 列表"""
        import shutil
        if not shutil.which("pwsh"):
            pytest.skip("pwsh.exe 不在 PATH 中")
        pty = SubprocessBackend(
            "echo hello", cols=80, rows=24, shell="pwsh",
        )
        try:
            pty._proc.wait(timeout=5)
            args = pty._proc.args
            assert isinstance(args, list)
            assert len(args) == 3
            assert "pwsh" in args[0].lower()
            assert args[1] == "-Command"
            assert args[2] == "echo hello"
        finally:
            pty.close()

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="非 cmd 的 shell 映射仅 Windows 生效",
    )
    def test_shell_bash_constructs_list(self):
        """shell='bash' 时构建 [bash.exe, -c, command] 列表"""
        import shutil
        if not shutil.which("bash"):
            pytest.skip("bash.exe 不在 PATH 中")
        pty = SubprocessBackend(
            "echo hello", cols=80, rows=24, shell="bash",
        )
        try:
            pty._proc.wait(timeout=5)
            args = pty._proc.args
            assert isinstance(args, list)
            assert len(args) == 3
            assert "bash" in args[0].lower()
            assert args[1] == "-c"
            assert args[2] == "echo hello"
        finally:
            pty.close()

    def test_unknown_shell_falls_back_to_cmd(self):
        """不认识的 shell 值回退到 cmd.exe（shell=True）"""
        pty = SubprocessBackend(
            "echo hello", cols=80, rows=24, shell="unknown_shell_name",
        )
        try:
            pty._proc.wait(timeout=5)
            # 不在 _SHELL_MAP 中 → shell_spec=None → shell=True
            assert pty._proc.args == "echo hello"
        finally:
            pty.close()

    def test_shell_with_list_command_noop(self):
        """列表命令下 shell 参数被忽略（不走 Subprocess 的 shell 选择）"""
        pty = SubprocessBackend(
            [sys.executable, "-c", "print('test')"],
            shell="powershell",
        )
        try:
            pty._proc.wait(timeout=5)
            # 列表命令 → use_shell=False → shell=False，原样传递
            assert pty._proc.args == [sys.executable, "-c", "print('test')"]
        finally:
            pty.close()

    def test_exit_code_with_shell_cmd(self):
        """shell='cmd' 时退出码仍正确"""
        pty = SubprocessBackend(
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
        from src.client.api import PtyClient

        client = PtyClient()
        resp = client.cmd_exec(
            session_id="test",
            command='{"data":"echo hello"}',
            pty=True,
            shell="powershell",
        )

        assert resp["type"] == "error"
        assert "不能同时使用" in resp["error"]

    def test_pty_without_shell_ok(self, monkeypatch):
        """--pty 不带 --shell 时不触发冲突（后续请求交由守护进程处理）"""
        from src.client.api import PtyClient

        # 阻止真实的共享内存请求（冲突检测通过后仍会走到 _send）
        monkeypatch.setattr(
            PtyClient, "_send",
            lambda self, msg: (_ for _ in ()).throw(Exception("mock")),
        )

        client = PtyClient()
        with pytest.raises(Exception, match="mock"):
            client.cmd_exec(
                session_id="test",
                command='{"data":"echo hello"}',
                pty=True,
                shell=None,
            )

    def test_shell_without_pty_ok(self, monkeypatch):
        """--shell 不带 --pty 时不触发冲突"""
        from src.client.api import PtyClient

        monkeypatch.setattr(
            PtyClient, "_send",
            lambda self, msg: (_ for _ in ()).throw(Exception("mock")),
        )

        client = PtyClient()
        with pytest.raises(Exception, match="mock"):
            client.cmd_exec(
                session_id="test",
                command='{"data":"echo hello"}',
                pty=False,
                shell="pwsh",
            )
