"""UnixPseudoTerminal — 基于 os.openpty + fork 的 PTY 实现

跨平台骨架：核心 PTY 读写逻辑在本文件，进程树管理委托给 process.UnixProcessMonitor。
"""

import logging
import os
import errno
import signal
from typing import Optional, List

from ..base import PseudoTerminal
from ...config.common import DEFAULT_COLS, DEFAULT_ROWS
from .process import UnixProcessMonitor

_logger = logging.getLogger("pty-unix")


class UnixPseudoTerminal(PseudoTerminal):
    """Unix 伪终端（os.openpty + os.fork + os.execvpe）

    使用标准的 Unix PTY 接口创建伪终端。
    子进程通过 os.setsid() 创建新会话，进程树通过 pgid 追踪。
    """

    def __init__(self, command, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS, env=None, cwd=None):
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

        fcntl.fcntl(self._master, fcntl.F_SETFL,
                    fcntl.fcntl(self._master, fcntl.F_GETFL) | os.O_NONBLOCK)
        _logger.debug("UnixPseudoTerminal: master_fd=%d", self._master)

        self._monitor = UnixProcessMonitor(self._child_pid)
        self._cols = cols
        self._rows = rows
        self._exit_code: Optional[int] = None
        self._reaped = False

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

    def resize(self, cols: int, rows: int):
        import fcntl
        import struct
        import termios
        try:
            fcntl.ioctl(self._master, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
            self._cols = cols
            self._rows = rows
            _logger.debug("resize: %dx%d", cols, rows)
        except Exception as e:
            _logger.warning("resize failed: %s", e)

    def kill_tree(self):
        self._monitor.kill_tree()

    def close(self):
        _logger.info("close: pid=%d", self._child_pid)
        self._reap_child()
        try:
            os.close(self._master)
        except Exception as e:
            _logger.warning("close master error: %s", e)
        self._monitor.close()

    def _reap_child(self):
        if self._reaped:
            return
        if not self._monitor.is_child_alive():
            self._try_waitpid()
            return
        self._monitor.kill_tree()
        import time
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self._try_waitpid() is not None:
                return
            if not self._monitor.is_child_alive():
                self._try_waitpid()
                return
            time.sleep(0.1)
        self._try_waitpid()

    def _try_waitpid(self) -> Optional[int]:
        if self._reaped:
            return self._exit_code
        try:
            pid, status = os.waitpid(self._child_pid, os.WNOHANG)
            if pid == 0:
                return None
            self._reaped = True
            if os.WIFEXITED(status):
                self._exit_code = os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                self._exit_code = -os.WTERMSIG(status)
            return self._exit_code
        except ChildProcessError:
            self._reaped = True
            return self._exit_code
        except Exception:
            return None

    def get_type(self) -> str:
        return "unix-pty"

    def get_child_pid(self):
        return self._child_pid

    def get_exit_code(self) -> Optional[int]:
        if self._exit_code is not None:
            return self._exit_code
        return self._try_waitpid()

    def get_process_list(self) -> List[int]:
        return self._monitor.get_process_list()

    def get_job_notifications(self) -> list:
        return self._monitor.drain_notifications()

    def poll_gui_windows(self) -> List[dict]:
        return []

    def close_gui_window(self, hwnd: int) -> bool:
        return False

    def get_gui_windows(self) -> List[dict]:
        return []

    def get_child_process_exit_code(self, pid: int) -> Optional[int]:
        if pid == self._child_pid:
            return self.get_exit_code()
        try:
            p, status = os.waitpid(pid, os.WNOHANG)
            if p == 0:
                return None
            if os.WIFEXITED(status):
                return os.WEXITSTATUS(status)
            if os.WIFSIGNALED(status):
                return -os.WTERMSIG(status)
        except Exception:
            pass
        return None
