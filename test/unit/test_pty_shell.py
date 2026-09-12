"""SubprocessBackend 解释器选择测试

验证 --shell 参数能否正确切换命令解释器（按平台提供的映射）。
"""

import functools
import shutil
import subprocess
import sys
import pytest

from src.backend.subprocess import SubprocessBackend


@functools.lru_cache(maxsize=None)
def _shell_executable(exe: str, arg: str) -> bool:
    """解释器是否存在且确实能跑一条命令（每个解释器只探测一次）

    "在 PATH 里"不等于"可用"：例如 WSL 未初始化时 bash.exe 存在但会挂住。
    探测失败/超时 → False，让相关用例 skip 而非让套件变红。
    """
    if not shutil.which(exe):
        return False
    try:
        proc = subprocess.run(
            [exe, arg, "echo ok"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


class TestSubprocessShellSelection:
    """SubprocessBackend shell 选择测试

    验证平台映射（shell_map / shell_choices）、shell=None 默认行为、
    指定 shell 的 Popen 构建。
    """

    # 解释器名 → 期望的命令参数（None 表示交给 shell=True）
    KNOWN_SHELLS = {"cmd": None, "sh": None, "powershell": "-Command",
                    "pwsh": "-Command", "bash": "-c"}

    def test_shell_map_non_empty_and_known(self):
        """当前平台映射非空，且只含已知解释器"""
        shell_map = SubprocessBackend.shell_map()
        assert shell_map
        assert set(shell_map) <= set(self.KNOWN_SHELLS)

    def test_shell_true_entry_maps_to_none(self):
        """cmd（Windows）/ sh（POSIX）映射为 None → 由 shell=True 承担"""
        shell_true = "cmd" if sys.platform == "win32" else "sh"
        assert SubprocessBackend.shell_map()[shell_true] is None

    @pytest.mark.parametrize("name", ["powershell", "pwsh", "bash"])
    def test_explicit_shell_spec_format(self, name):
        """显式解释器映射为 [可执行文件, 参数]，参数按平台约定"""
        spec = SubprocessBackend.shell_map()[name]
        assert isinstance(spec, list)
        assert len(spec) == 2
        assert name in spec[0].lower()
        assert spec[1] == self.KNOWN_SHELLS[name]

    def test_shell_choices_match_map(self):
        """shell_choices 与 shell_map 一致（CLI 提示与实际支持同源）"""
        assert SubprocessBackend.shell_choices() == tuple(
            SubprocessBackend.shell_map())

    def test_unsupported_shell_raises_not_silently_switches(self):
        """平台不支持的解释器名 → 明确报错，绝不静默换成别的解释器"""
        with pytest.raises(RuntimeError):
            SubprocessBackend("echo hi", shell="definitely-not-a-shell")

    def test_list_command_never_uses_shell(self):
        """列表命令 → shell=False，原样执行"""
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

    def test_shell_true_name_on_string_command(self):
        """平台自带的 shell=True 解释器（Windows cmd / POSIX sh）→ 命令保持字符串"""
        shell_true = "cmd" if sys.platform == "win32" else "sh"
        pty = SubprocessBackend(
            "echo hello", cols=80, rows=24, shell=shell_true,
        )
        try:
            pty._proc.wait(timeout=5)
            # shell=True 时 command 保持字符串，不走列表
            assert pty._proc.args == "echo hello"
        finally:
            pty.close()

    @pytest.mark.parametrize("name,fragment,flag", [
        ("powershell", "powershell", "-Command"),
        ("pwsh", "pwsh", "-Command"),
        ("bash", "bash", "-c"),
    ])
    def test_explicit_shell_constructs_list(self, name, fragment, flag):
        """显式解释器 → 构建 [可执行文件, 参数, command] 列表（shell=False）

        只校验构建出的命令行，不等待解释器真的跑完；本机解释器缺失或
        坏掉（如 WSL 未初始化时 bash.exe 会挂住）时跳过，而不是让套件红。
        """
        spec = SubprocessBackend.shell_map()[name]
        if not _shell_executable(spec[0], spec[1]):
            pytest.skip(f"{spec[0]} 在本机不可执行")
        pty = SubprocessBackend("echo hello", cols=80, rows=24, shell=name)
        try:
            args = pty._proc.args
            assert isinstance(args, list)
            assert len(args) == 3
            assert fragment in args[0].lower()
            assert args[1] == flag
            assert args[2] == "echo hello"
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
