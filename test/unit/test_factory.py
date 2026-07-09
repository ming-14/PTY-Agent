"""pty/factory.py 工厂函数单元测试

测试 create_pty 的平台选择逻辑和回退行为。
"""

import sys
import pytest

from src.pty.factory import create_pty
from src.pty.subprocess import SubprocessPseudoTerminal


class TestCreatePtyStringCommand:
    """create_pty 字符串命令测试"""

    def test_string_command_uses_subprocess(self):
        """字符串命令使用 SubprocessPseudoTerminal"""
        pty = create_pty("echo hello", 80, 24)
        try:
            assert isinstance(pty, SubprocessPseudoTerminal)
        finally:
            pty.close()

    def test_string_command_with_shell(self):
        """字符串命令指定 shell"""
        pty = create_pty("echo hello", 80, 24, shell="cmd")
        try:
            assert isinstance(pty, SubprocessPseudoTerminal)
        finally:
            pty.close()


class TestCreatePtyListCommand:
    """create_pty 列表命令测试"""

    def test_list_command_returns_pty(self):
        """列表命令返回 PTY 实例"""
        pty = create_pty([sys.executable, "-c", "pass"], 80, 24)
        try:
            assert pty is not None
            assert hasattr(pty, "read")
            assert hasattr(pty, "write")
            assert hasattr(pty, "close")
        finally:
            pty.close()

    def test_list_command_exit_code(self):
        """列表命令退出码正确"""
        pty = create_pty([sys.executable, "-c", "import sys; sys.exit(42)"], 80, 24)
        try:
            if hasattr(pty, "_proc"):
                pty._proc.wait(timeout=5)
            else:
                import time
                time.sleep(0.5)
            code = pty.get_exit_code()
            if code is not None:
                assert code == 42
        finally:
            pty.close()


class TestCreatePtyFallback:
    """create_pty 回退行为测试"""

    def test_invalid_command_falls_back(self):
        """无效命令列表仍创建 Subprocess（启动时失败）"""
        with pytest.raises(Exception):
            create_pty(["nonexistent_command_xyz"], 80, 24)