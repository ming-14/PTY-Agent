"""UnixPseudoTerminal — 基于 os.openpty + fork 的 PTY 实现"""

import logging
import os
import errno
from typing import Optional

from .base import PseudoTerminal

_logger = logging.getLogger("pty-unix")


class UnixPseudoTerminal(PseudoTerminal):
    """Unix 伪终端（os.openpty + os.fork + os.execvpe）

    使用标准的 Unix PTY 接口创建伪终端，支持终端尺寸设置。
    """

    def __init__(self, command, cols: int = 80, rows: int = 24, env=None, cwd=None):
        import fcntl
        import struct
        import termios

        self._master, slave = os.openpty()
        self._child_pid = os.fork()
        _logger.info("UnixPseudoTerminal: forked pid=%d cmd=%r", self._child_pid, command)
        if self._child_pid == 0:
            try:
                os.close(self._master)
                for fd in (0, 1, 2):
                    os.dup2(slave, fd)
                if slave not in (0, 1, 2):
                    os.close(slave)
                fcntl.ioctl(0, termios.TIOCSWINSZ,
                            struct.pack("HHHH", rows, cols, 0, 0))
                os.setsid()
                if cwd:
                    os.chdir(cwd)
                e = os.environ.copy()
                if env:
                    e.update(env)
                os.execvpe(command[0], command, e)
            except Exception as ex:
                _logger.error("UnixPseudoTerminal: child exec failed: %s", ex)
                os._exit(1)
        os.close(slave)

        # 设置非阻塞
        fcntl.fcntl(self._master, fcntl.F_SETFL,
                    fcntl.fcntl(self._master, fcntl.F_GETFL) | os.O_NONBLOCK)
        _logger.debug("UnixPseudoTerminal: master_fd=%d", self._master)

    def read(self, n: int = 65536) -> bytes:
        try:
            data = os.read(self._master, n)
            if data:
                _logger.debug("read: %d bytes", len(data))
            return data
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return b""
            _logger.warning("read error: %s", e)
            raise

    def drain(self, max_bytes: int = 65536) -> bytes:
        """排空 PTY master 中当前所有就绪数据（非阻塞 os.read 循环）"""
        chunks = []
        total = 0
        while True:
            try:
                more = os.read(self._master, max_bytes)
                if not more:
                    break
                chunks.append(more)
                total += len(more)
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    break
                raise
        if total:
            _logger.debug("drain: %d total bytes", total)
        return b"".join(chunks)

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        _logger.debug("write: %d bytes", len(data))
        os.write(self._master, data)

    def fileno(self):
        return self._master

    def kill_tree(self):
        """强杀进程树：发送 SIGKILL"""
        try:
            os.kill(self._child_pid, 9)
        except Exception:
            pass

    def close(self):
        _logger.info("close: pid=%d", self._child_pid)
        try:
            os.close(self._master)
        except Exception as e:
            _logger.warning("close master error: %s", e)
        try:
            os.waitpid(self._child_pid, os.WNOHANG)
        except Exception as e:
            _logger.warning("waitpid error: %s", e)

    def get_type(self) -> str:
        """返回 PTY 后端类型标识"""
        return "unix-pty"

    def get_child_pid(self):
        return self._child_pid

    def get_exit_code(self) -> Optional[int]:
        """获取子进程退出码

        通过 os.waitpid 非阻塞获取子进程的退出状态。

        Returns:
            退出码（int），若进程仍在运行则返回 None。
        """
        try:
            pid, status = os.waitpid(self._child_pid, os.WNOHANG)
            if pid == 0:
                return None
            if os.WIFEXITED(status):
                return os.WEXITSTATUS(status)
            if os.WIFSIGNALED(status):
                # 信号终止：返回负的信号编号
                return -os.WTERMSIG(status)
            return None
        except ChildProcessError:
            # 子进程已回收，可能已在 close() 中被 waitpid
            return None
        except Exception:
            return None
