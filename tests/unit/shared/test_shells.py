"""common/shells.py 的 wrap_command 与 exec_handler._wrap_with_shell 单元测试"""

import pytest

from src.common.shells import _SHELL_WRAP, detect_available_shells, wrap_command
from src.daemon.handlers.exec_handler import _wrap_with_shell


def _skip_if_shell_missing(shell: str):
    """当前平台 PATH 中无该 shell 时跳过（cmd/pwsh/node 等平台专属可执行文件）"""
    if not detect_available_shells().get(shell):
        pytest.skip(f"shell {shell!r} 在当前平台 PATH 中不可用")


class TestWrapCommand:
    def test_str_command_kept_verbatim(self):
        """str 命令原样传给 shell：操作符/引号保真"""
        result = wrap_command("echo a && echo b", "bash")
        assert result[0].endswith(("bash", "bash.exe"))
        assert result[1] == "-c"
        assert result[2] == "echo a && echo b"

    def test_str_command_cmd(self):
        _skip_if_shell_missing("cmd")
        result = wrap_command("echo x && echo y", "cmd")
        assert result[1] == "/c"
        assert result[2] == "echo x && echo y"

    def test_posix_shell_quotes(self):
        """list 输入（web 端兼容路径）：bash 类 shell shlex.join 重组"""
        result = wrap_command(["python", "-c", "print('a | b')"], "bash")
        assert result[0].endswith(("bash", "bash.exe"))
        assert result[1] == "-c"
        import shlex

        assert shlex.split(result[2]) == ["python", "-c", "print('a | b')"]

    def test_python_interpreter_str(self):
        """python -c 代码原样传递，不包裹引号"""
        result = wrap_command("print(1+1)", "python")
        assert result[1] == "-c"
        assert result[2] == "print(1+1)"

    def test_cmd_shell(self):
        """cmd：/c + Windows 命令行规则"""
        _skip_if_shell_missing("cmd")
        result = wrap_command(["echo", "hello world"], "cmd")
        assert result[1] == "/c"
        assert result[2] == 'echo "hello world"'

    def test_pwsh_shell(self):
        _skip_if_shell_missing("pwsh")
        result = wrap_command(["Write-Output", "hello"], "pwsh")
        assert result[1] == "-Command"

    def test_node_interpreter(self):
        _skip_if_shell_missing("node")
        result = wrap_command(["console.log('hi')"], "node")
        assert result[1] == "-e"

    def test_unsupported_shell(self):
        with pytest.raises(ValueError, match="不支持的 shell"):
            wrap_command(["echo"], "nosuch_shell_name")

    def test_shell_not_found_in_path(self):
        """支持但 PATH 中不可用的 shell（如 Windows 上的 zsh）报错"""
        available = detect_available_shells()
        missing = [name for name in _SHELL_WRAP if not available.get(name)]
        if not missing:
            pytest.skip("所有支持 shell 均可用，无法测不可用分支")
        with pytest.raises(ValueError, match="找不到 shell"):
            wrap_command(["echo"], missing[0])


class TestWrapWithShell:
    def test_none_no_wrap(self):
        assert _wrap_with_shell(["echo", "hi"], None) == ["echo", "hi"]

    def test_list_wrapped(self):
        result = _wrap_with_shell(["echo", "a | b"], "bash")
        assert result[1] == "-c"

    def test_str_wrapped_verbatim(self):
        """str 命令原样传给 shell（操作符保真）"""
        _skip_if_shell_missing("cmd")
        result = _wrap_with_shell("echo hi", "cmd")
        assert result[0].endswith(("cmd", "cmd.exe"))
        assert result[1] == "/c"
        assert result[2] == "echo hi"

    def test_str_operators_preserved(self):
        """str 命令的 && 不被引号化（B3 回归）"""
        result = _wrap_with_shell("echo a && echo b", "bash")
        assert result[2] == "echo a && echo b"

    def test_python_str_no_quote_wrap(self):
        """python -c 代码不被包引号（D1 回归）"""
        result = _wrap_with_shell("print(1+1)", "python")
        assert result[2] == "print(1+1)"

    def test_unsupported_raises(self):
        with pytest.raises(ValueError):
            _wrap_with_shell(["echo"], "not_a_shell")