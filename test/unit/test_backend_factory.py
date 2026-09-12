"""backend/factory.py 工厂函数单元测试

验证两种后端模式的显式创建入口（subprocess 与 tty 分离、无回退链）：
- create_subprocess —— 纯管道子进程（字符串命令）
- create_tty      —— 真实终端（列表命令）
"""

import sys
import pytest

from src.backend.factory import create_subprocess, create_tty
from src.backend.subprocess import SubprocessBackend


class TestCreateSubprocess:
    """create_subprocess —— 纯管道子进程"""

    def test_string_command_returns_subprocess_backend(self):
        pty = create_subprocess("echo hello", cols=80, rows=24)
        try:
            assert isinstance(pty, SubprocessBackend)
        finally:
            pty.close()

    def test_string_command_with_shell(self):
        pty = create_subprocess("echo hello", shell="cmd", cols=80, rows=24)
        try:
            assert isinstance(pty, SubprocessBackend)
        finally:
            pty.close()

    def test_list_command_also_subprocess(self):
        """列表命令在 subprocess 模式下仍走管道（无终端）"""
        pty = create_subprocess([sys.executable, "-c", "pass"], cols=80, rows=24)
        try:
            assert isinstance(pty, SubprocessBackend)
            assert pty.get_type() == "subprocess"
        finally:
            pty.close()


class TestCreateTty:
    """create_tty —— 真实终端（无回退）"""

    def test_returns_tty_with_io(self):
        pty = create_tty([sys.executable, "-c", "pass"], cols=80, rows=24)
        try:
            assert hasattr(pty, "read")
            assert hasattr(pty, "write")
            assert hasattr(pty, "close")
        finally:
            pty.close()

    def test_tty_type(self):
        pty = create_tty([sys.executable, "-c", "pass"], cols=80, rows=24)
        try:
            assert pty.get_type() in ("win-conpty", "unix-pty")
        finally:
            pty.close()

    def test_invalid_command_raises_no_fallback(self):
        """创建失败直接抛错，不回退 subprocess"""
        with pytest.raises(Exception):
            create_tty(["nonexistent_command_xyz"], cols=80, rows=24)
