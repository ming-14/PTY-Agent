"""PTY 后端 drain() 方法单元测试

测试各后端 drain() 的正确性：
- 基类默认返回 b""
- Subprocess 继承基类，返回 b""
- Unix PTY 非阻塞 os.read 循环排空
- Windows ConPTY PeekNamedPipe + ReadFile 排空（仅 Windows）
- Windows ConDrv 重叠 I/O + PeekNamedPipe 排空（仅 Windows）
"""

import os
import sys
import errno
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


class TestSubprocessPseudoTerminalDrain:
    """SubprocessPseudoTerminal drain() 测试

    子进程管道是阻塞式 subprocess.PIPE.stdout.read(n)，
    无法非阻塞排空，因此 drain() 继承基类返回 b""。
    """

    def test_subprocess_drain_returns_empty(self):
        """SubprocessPseudoTerminal.drain() 返回 b""（继承基类）"""
        from src.pty.subprocess import SubprocessPseudoTerminal

        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "print('hello')"],
        )
        try:
            pty._proc.wait(timeout=5)
            pty._proc.stdout.read()  # 排空管道
            assert pty.drain() == b""
            assert pty.drain(4096) == b""
        finally:
            pty.close()

    def test_subprocess_read_still_works(self):
        """drain() 不破坏正常的 read()"""
        from src.pty.subprocess import SubprocessPseudoTerminal

        pty = SubprocessPseudoTerminal(
            [sys.executable, "-u", "-c", "print('hello world')"],
        )
        try:
            pty._proc.stdin.close()
            # drain 不应影响管道状态
            pty.drain()
            data = pty.read(65536)
            assert b"hello world" in data
        finally:
            pty.close()


@pytest.mark.skipif(
    sys.platform not in ("linux", "linux2", "darwin"),
    reason="UnixPseudoTerminal 仅在 Unix 平台可用",
)
class TestUnixPseudoTerminalDrain:
    """UnixPseudoTerminal drain() 测试

    Unix PTY 使用 os.O_NONBLOCK 模式，os.read 立即返回当前可读数据。
    drain() 应循环读取直到无数据，拼接所有 chunk 返回。
    """

    def test_drain_loops_until_empty(self, monkeypatch):
        """drain() 循环读取直到 os.read 返回 b"""""
        from src.pty.unix import UnixPseudoTerminal

        read_results = [b"chunk1", b"chunk2", b"chunk3", b""]

        def mock_read(fd, n):
            return read_results.pop(0)

        monkeypatch.setattr(os, "read", mock_read)

        pty = UnixPseudoTerminal()
        try:
            result = pty.drain(65536)
            assert result == b"chunk1chunk2chunk3"
        finally:
            pty.close()

    def test_drain_handles_eagain_early(self, monkeypatch):
        """drain() 遇到 EAGAIN 即停止"""
        from src.pty.unix import UnixPseudoTerminal

        call_count = 0

        def mock_read(fd, n):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"data"
            raise OSError(errno.EAGAIN, "EAGAIN")

        monkeypatch.setattr(os, "read", mock_read)

        pty = UnixPseudoTerminal()
        try:
            result = pty.drain(65536)
            assert result == b"data"
            assert call_count == 2
        finally:
            pty.close()

    def test_drain_does_not_block_on_empty(self, monkeypatch):
        """drain() 在管道无数据时立即返回 b"""""
        from src.pty.unix import UnixPseudoTerminal

        def mock_read(fd, n):
            raise OSError(errno.EAGAIN, "EAGAIN")

        monkeypatch.setattr(os, "read", mock_read)

        pty = UnixPseudoTerminal()
        try:
            result = pty.drain(65536)
            assert result == b""
        finally:
            pty.close()

    def test_drain_multiple_chunks(self, monkeypatch):
        """drain() 拼接 5 个以上小 chunk 仍正确"""
        from src.pty.unix import UnixPseudoTerminal

        chunks = [f"chunk{i} ".encode() for i in range(10)]

        def mock_read(fd, n):
            return chunks.pop(0) if chunks else b""

        monkeypatch.setattr(os, "read", mock_read)

        pty = UnixPseudoTerminal()
        try:
            result = pty.drain(65536)
            expected = b"chunk0 chunk1 chunk2 chunk3 chunk4 chunk5 chunk6 chunk7 chunk8 chunk9 "
            assert result == expected
        finally:
            pty.close()

    def test_drain_respects_max_bytes(self, monkeypatch):
        """drain 每次读取传入 max_bytes"""
        from src.pty.unix import UnixPseudoTerminal

        def mock_read(fd, n):
            assert n == 4096
            raise OSError(errno.EAGAIN, "EAGAIN")

        monkeypatch.setattr(os, "read", mock_read)

        pty = UnixPseudoTerminal()
        try:
            pty.drain(max_bytes=4096)
        finally:
            pty.close()

    def test_read_and_drain_work_together(self, monkeypatch):
        """read() + drain() 组合使用，数据不丢失"""
        from src.pty.unix import UnixPseudoTerminal

        read_results = [b"part1", b"part2", b"part3", b"", b""]
        idx = [0]

        def mock_read(fd, n):
            i = idx[0]
            idx[0] += 1
            if i >= len(read_results):
                raise OSError(errno.EAGAIN, "EAGAIN")
            return read_results[i]

        monkeypatch.setattr(os, "read", mock_read)

        pty = UnixPseudoTerminal()
        try:
            data = pty.read(65536)
            assert data == b"part1"
            extra = pty.drain(65536)
            assert extra == b"part2part3"
            assert data + extra == b"part1part2part3"
        finally:
            pty.close()


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows ConPTY 仅 Windows 平台",
)
class TestWindowsPseudoTerminalDrain:
    """WindowsPseudoTerminal (kernel32_api) drain() 测试

    使用 PeekNamedPipe 非阻塞查询管道就绪数据量，
    有数据时发起 ReadFile 读取。
    """

    def test_drain_type_and_callable(self):
        """drain() 方法存在且可调用"""
        from src.pty.windows.kernel32_api import WindowsPseudoTerminal
        assert hasattr(WindowsPseudoTerminal, "drain")
        assert callable(WindowsPseudoTerminal.drain)

    def test_drain_no_data(self):
        """子进程未输出时 drain() 返回 b"""""
        from src.pty.windows.kernel32_api import WindowsPseudoTerminal

        try:
            pty = WindowsPseudoTerminal(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cols=80, rows=24,
            )
        except OSError:
            pytest.skip("CreatePseudoConsole 不可用")

        try:
            result = pty.drain(65536)
            assert isinstance(result, bytes)
            # 子进程 sleep，drain 应返回 b""
            assert result == b""
        finally:
            try:
                pty.close()
            except Exception:
                pass


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="ConDrv 仅 Windows 平台",
)
class TestConDrvPseudoTerminalDrain:
    """Windows ConDrv drain() 测试"""

    def test_drain_method_exists(self):
        """ConDrvPseudoTerminal 有 drain() 方法"""
        from src.pty.windows.condrv import ConDrvPseudoTerminal
        assert hasattr(ConDrvPseudoTerminal, "drain")
        assert callable(ConDrvPseudoTerminal.drain)


class TestDrainInterface:
    """drain() 接口完整性测试（跨平台）"""

    def test_base_pty_has_drain(self):
        """基类 PseudoTerminal 实现了 drain() 方法"""
        assert hasattr(PseudoTerminal, "drain")
        assert callable(PseudoTerminal.drain)

    def test_subprocess_has_drain(self):
        """SubprocessPseudoTerminal 有 drain()（通过继承）"""
        from src.pty.subprocess import SubprocessPseudoTerminal
        assert hasattr(SubprocessPseudoTerminal, "drain")

    def test_unix_pty_has_drain(self):
        """UnixPseudoTerminal 有 drain()"""
        from src.pty.unix import UnixPseudoTerminal
        assert hasattr(UnixPseudoTerminal, "drain")

    def test_drain_returns_bytes(self):
        """基类 drain() 返回 bytes"""
        pty = PseudoTerminal()
        result = pty.drain(65536)
        assert isinstance(result, bytes)
        assert result == b""
