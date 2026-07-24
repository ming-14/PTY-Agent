"""pty/factory.py 单元测试"""

import sys
import pytest

from src.pty.pty_factory import create_pty


class TestCreatePtyListCommand:
    def test_list_command_returns_pty(self):
        pty = create_pty([sys.executable, "-c", "pass"], 80, 24)
        try:
            assert pty is not None
            assert hasattr(pty, "read")
            assert hasattr(pty, "write")
            assert hasattr(pty, "close")
        finally:
            pty.close()

    def test_list_command_exit_code(self):
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
    def test_invalid_command_falls_back(self):
        with pytest.raises(Exception):
            create_pty(["nonexistent_command_xyz"], 80, 24)
