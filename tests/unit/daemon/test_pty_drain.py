"""PTY 后端 drain() 方法单元测试

测试统一 wezterm-py 后端（WeztermPseudoTerminal）的 drain()：
- 基类默认返回 b""
- wezterm Pty 内部 reader 线程 + 缓冲队列；drain 以 timeout=0 非阻塞读取
  当前已就绪缓冲。
"""

import sys
import pytest

from src.pty.base import PseudoTerminal


class TestPseudoTerminalDrain:
    """PseudoTerminal 基类 drain() 测试"""

    def test_base_drain_returns_empty(self):
        """基类 drain() 默认返回 b"""""
        pty = PseudoTerminal()
        assert pty.drain() == b""
        assert pty.drain(1024) == b""

    def test_base_drain_has_correct_signature(self):
        """基类 drain() 接受 max_bytes 参数"""
        pty = PseudoTerminal()
        result = pty.drain(max_bytes=4096)
        assert result == b""


class TestWeztermPseudoTerminalDrain:
    """WeztermPseudoTerminal (wezterm-pty) drain() 测试（跨平台统一后端）

    wezterm Pty 内部 reader 线程 + 缓冲队列；drain 以 timeout=0 非阻塞读取
    当前已就绪缓冲。
    """

    def test_drain_method_exists(self):
        """WeztermPseudoTerminal 有 drain() 方法"""
        from src.pty.wezterm_pty import WeztermPseudoTerminal
        assert hasattr(WeztermPseudoTerminal, "drain")
        assert callable(WeztermPseudoTerminal.drain)

    def test_drain_returns_bytes(self):
        """子进程运行时 drain() 返回 bytes（可能含缓冲输出）"""
        from src.pty.wezterm_pty import WeztermPseudoTerminal

        try:
            pty = WeztermPseudoTerminal(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cols=80, rows=24,
            )
        except RuntimeError:
            pytest.skip("wezterm-py PTY 不可用")

        try:
            result = pty.drain(65536)
            assert isinstance(result, bytes)
        finally:
            try:
                pty.close()
            except Exception:
                pass


class TestDrainInterface:
    """drain() 接口完整性测试（跨平台）"""

    def test_base_pty_has_drain(self):
        """基类 PseudoTerminal 实现了 drain() 方法"""
        assert hasattr(PseudoTerminal, "drain")
        assert callable(PseudoTerminal.drain)

    def test_wezterm_pty_has_drain(self):
        """WeztermPseudoTerminal 有 drain()"""
        from src.pty.wezterm_pty import WeztermPseudoTerminal
        assert hasattr(WeztermPseudoTerminal, "drain")

    def test_drain_returns_bytes(self):
        """基类 drain() 返回 bytes"""
        pty = PseudoTerminal()
        result = pty.drain(65536)
        assert isinstance(result, bytes)
        assert result == b""