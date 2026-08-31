"""UnixPseudoTerminal — 基于 os.openpty + fork + execvpe 的 PTY 实现

平台对齐能力（与 Windows 后端对称）：
- get_process_list()  — 通过 /proc BFS 遍历返回全进程树 PID
- get_child_process_exit_code() — 直接子进程退出码查询（孙进程返回 None）
- kill_tree()         — 后代优先逐个 SIGKILL，最后杀根进程
- get_job_notifications() / get_gui_windows() — 返回空（无等价机制）
"""

import logging
import os
import errno
import signal
from typing import List, Optional

from ..base import PseudoTerminal, ProcessEvent

_logger = logging.getLogger("pty-unix")


class UnixPseudoTerminal(PseudoTerminal):
    """Unix 伪终端（os.openpty + os.fork + os.execvpe）

    使用标准的 Unix PTY 接口创建伪终端，支持终端尺寸设置。
    通过 UnixProcessTracker 实现进程树追踪。
    """

    def __init__(self, command, cols: int = 80, rows: int = 24,
                 env=None, cwd=None):
        # POSIX 特有导入（延迟，避免 Windows 上 import 失败）
        import fcntl
        import struct
        import termios

        self._master, slave = os.openpty()
        self._child_pid = os.fork()
        _logger.info("UnixPseudoTerminal: forked pid=%d cmd=%r",
                      self._child_pid, command)
        if self._child_pid == 0:
            # ── 子进程 ──
            try:
                os.close(self._master)
                for fd in (0, 1, 2):
                    os.dup2(slave, fd)
                if slave not in (0, 1, 2):
                    os.close(slave)
                fcntl.ioctl(0, termios.TIOCSWINSZ,
                            struct.pack("HHHH", rows, cols, 0, 0))
                # 创建独立会话与进程组（对齐 Windows Job Object 的进程树管理）。
                # setsid() 同时完成会话领导与进程组领导，无需再 setpgid。
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
        # ── 父进程 ──
        os.close(slave)
        # 设置非阻塞
        fcntl.fcntl(self._master, fcntl.F_SETFL,
                    fcntl.fcntl(self._master, fcntl.F_GETFL) | os.O_NONBLOCK)
        _logger.debug("UnixPseudoTerminal: master_fd=%d", self._master)

        # ── 进程树追踪器（对齐 Windows Job Object 能力）──
        from .tracker import UnixProcessTracker
        self._tracker = UnixProcessTracker(self._child_pid)
        # ── 退出码缓存（避免 waitpid 竞争）──
        self._exit_code: Optional[int] = None

    # ── I/O ──

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

    # ── 生命周期 ──

    def kill_tree(self):
        """强杀进程树：通过 UnixProcessTracker 杀整个进程树（后代优先）"""
        if self._tracker:
            try:
                self._tracker.kill_tree()
            except Exception as e:
                _logger.warning("kill_tree tracker error: %s", e)
        else:
            try:
                os.kill(self._child_pid, signal.SIGKILL)
            except Exception:
                pass

    def close(self):
        _logger.info("close: pid=%d", self._child_pid)
        try:
            os.close(self._master)
        except Exception as e:
            _logger.warning("close master error: %s", e)
        try:
            pid, status = os.waitpid(self._child_pid, os.WNOHANG)
            if pid and self._exit_code is None:
                # 若退出码尚未被获取，从 waitpid 状态中提取缓存
                if os.WIFEXITED(status):
                    self._exit_code = os.WEXITSTATUS(status)
                elif os.WIFSIGNALED(status):
                    self._exit_code = -os.WTERMSIG(status)
        except Exception as e:
            _logger.warning("waitpid error: %s", e)

    # ── 类型标识 ──

    def get_type(self) -> str:
        return "unix-pty"

    # ── 进程信息 ──

    def get_child_pid(self):
        return self._child_pid

    def get_exit_code(self) -> Optional[int]:
        """获取子进程退出码（waitpid 非阻塞 + 结果缓存）

        只对直接子进程调用一次 waitpid 并缓存结果，
        避免与 get_child_process_exit_code 竞争导致退出码丢失。
        """
        if self._exit_code is not None:
            return self._exit_code
        try:
            pid, status = os.waitpid(self._child_pid, os.WNOHANG)
            if pid == 0:
                return None
            if os.WIFEXITED(status):
                self._exit_code = os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                self._exit_code = -os.WTERMSIG(status)
            else:
                self._exit_code = 0
            return self._exit_code
        except ChildProcessError:
            return None
        except Exception:
            return None

    # ── 进程树追踪（对齐 Windows Job Object）──

    def get_process_list(self) -> List[int]:
        """获取进程树所有进程的 PID 列表

        通过 /proc BFS 遍历返回根进程及其所有后代 PID。
        Windows 对称能力：Job Object.QueryInformationJobObject。

        Returns:
            PID 列表。
        """
        if self._tracker:
            try:
                return self._tracker.get_process_list()
            except Exception as e:
                _logger.warning("get_process_list error: %s", e)
        pid = self.get_child_pid()
        return [pid] if pid is not None else []

    def get_child_process_exit_code(self, pid: int) -> Optional[int]:
        """查询子进程退出码

        Args:
            pid: 目标进程 PID。

        Returns:
            退出码（int），仅直接子进程（pid == child_pid）可查；
            孙进程返回 None（Unix 限制）。
        """
        # 对直接子进程：返回 get_exit_code 的缓存值（避免重复 waitpid 竞争）
        if pid == self._child_pid:
            return self.get_exit_code()
        # 孙进程：Unix 无法 waitpid，返回 None
        return None

    def get_job_notifications(self) -> List[ProcessEvent]:
        """获取实时进程事件通知

        Unix 无 Job Object IOCP 等价机制，返回空列表。
        接口保留以供 ProcessMonitor 统一调用。

        Returns:
            空列表。
        """
        return []

    # ---- GUI 窗口检测（Unix 无等价机制，继承基类返回空）----