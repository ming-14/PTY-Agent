"""pty/subprocess_pty.py 单元测试

用真实 Python 子进程（sys.executable）验证双管道 stdout/stderr 捕获、
stdin 写入、EOF 判定、退出码获取与 close 清理。
"""

import sys
import time

import pytest

from src.pty.subprocess_pty import SubprocessPseudoTerminal


def _read_until_eof(pty, timeout=5.0):
    """轮询读取 stdout/stderr 直到双流 EOF 或超时"""
    out, err = bytearray(), bytearray()
    deadline = time.time() + timeout
    while time.time() < deadline:
        o = pty.read()
        e = pty.read_stderr()
        if o:
            out.extend(o)
        if e:
            err.extend(e)
        if pty.is_eof():
            break
        time.sleep(0.02)
    return bytes(out), bytes(err)


class TestSubprocessPseudoTerminal:
    def test_get_type(self):
        pty = SubprocessPseudoTerminal([sys.executable, "-c", "pass"])
        assert pty.get_type() == "subprocess"
        pty.close()

    def test_stdout_stderr_separated(self):
        code = (
            "import sys; print('OUT1'); "
            "print('ERR1', file=sys.stderr); print('OUT2')"
        )
        pty = SubprocessPseudoTerminal([sys.executable, "-c", code])
        out, err = _read_until_eof(pty)
        assert b"OUT1" in out and b"OUT2" in out
        assert b"ERR1" in err
        assert b"ERR1" not in out  # 双流分离
        assert pty.is_eof()
        # EOF 后短暂等待进程 poll 更新退出码
        for _ in range(50):
            if pty.get_exit_code() is not None:
                break
            time.sleep(0.02)
        assert pty.get_exit_code() == 0
        pty.close()

    def test_stdin_write(self):
        code = (
            "import sys; line=sys.stdin.readline(); "
            "print('GOT:'+line.strip())"
        )
        pty = SubprocessPseudoTerminal([sys.executable, "-c", code])
        pty.write(b"hello\n")
        out, _ = _read_until_eof(pty)
        assert b"GOT:hello" in out
        pty.close()

    def test_child_pid(self):
        pty = SubprocessPseudoTerminal([sys.executable, "-c", "import time; time.sleep(1)"])
        assert isinstance(pty.get_child_pid(), int)
        assert pty.get_child_pid() > 0
        pty.close()

    def test_resize_raises(self):
        pty = SubprocessPseudoTerminal([sys.executable, "-c", "pass"])
        with pytest.raises(RuntimeError):
            pty.resize(100, 30)
        pty.close()

    def test_fileno_none(self):
        pty = SubprocessPseudoTerminal([sys.executable, "-c", "pass"])
        assert pty.fileno() is None
        pty.close()

    def test_close_idempotent(self):
        pty = SubprocessPseudoTerminal([sys.executable, "-c", "import time; time.sleep(10)"])
        pty.close()
        pty.close()  # 幂等，不抛异常