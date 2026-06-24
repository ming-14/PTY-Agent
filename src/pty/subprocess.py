"""SubprocessPseudoTerminal — 基于 subprocess 管道的 PTY 回退实现"""

import ctypes
import os
import shutil
import subprocess
import logging
from typing import Optional, List, Union
from ctypes import wintypes as W

from .base import PseudoTerminal
from ..config import IS_WINDOWS

if IS_WINDOWS:
    from .windows.job import ProcessJob
    from .windows.gui_monitor import GuiWindowMonitor, GuiWindowInfo
    from .windows.convars import K, _CloseHandle

# ── Windows 错误模式常量（禁止子进程弹出崩溃对话框）──
_SEM_FAILCRITICALERRORS     = 0x0001   # 禁止 critical-error-handler 消息框
_SEM_NOGPFAULTERRORBOX      = 0x0002   # 禁止一般保护错误消息框
_SEM_NOOPENFILEERRORBOX     = 0x8000   # 禁止文件打开失败消息框
_CREATE_NO_WINDOW           = 0x08000000  # 禁止为控制台程序创建可见窗口
_STARTF_USESHOWWINDOW       = 0x00000001
_SW_HIDE                    = 0

_logger = logging.getLogger("pty-subprocess")


def detect_available_shells() -> dict:
    """检测当前环境可用的 shell 解释器

    Returns:
        字典，键为 shell 名称，值为可执行文件路径（不可用则为 None）。
        例如: {"cmd": "C:\\Windows\\System32\\cmd.exe", "powershell": None, ...}
    """
    result = {}
    # cmd 在 Windows 上始终可用
    if IS_WINDOWS:
        cmd_path = shutil.which("cmd.exe")
        result["cmd"] = cmd_path or "cmd.exe"
    for name, spec in SubprocessPseudoTerminal._SHELL_MAP.items():
        if name == "cmd":
            continue
        if spec is None:
            continue
        exe = spec[0]
        result[name] = shutil.which(exe)
    return result


def format_shell_info() -> str:
    """格式化当前环境 shell 支持信息

    Returns:
        人类可读的 shell 支持信息字符串。
    """
    shells = detect_available_shells()
    parts = []
    for name, path in shells.items():
        if path:
            parts.append(f"{name} ({path})")
        else:
            parts.append(f"{name} (不可用)")
    return "可用 shell: " + ", ".join(parts)


class SubprocessPseudoTerminal(PseudoTerminal):
    """subprocess 管道模式

    使用 subprocess.Popen 的 stdin/stdout/stderr 管道进行交互。
    不支持真正的终端功能，作为最终保底方案。
    默认优先使用 powershell，不可用时回退至 cmd.exe，可通过 shell 参数切换解释器。
    """

    # 可选解释器映射：解释器名 → [可执行文件, 命令参数]
    _SHELL_MAP = {
        "cmd":        None,                         # subprocess shell=True → cmd.exe
        "powershell": ["powershell.exe", "-Command"],
        "pwsh":       ["pwsh.exe",      "-Command"],
        "bash":       ["bash.exe",      "-c"],
    }

    @classmethod
    def _resolve_default_shell(cls) -> str:
        """解析默认 shell：优先 PowerShell，不可用时回退至 cmd

        Returns:
            "powershell" 或 "cmd"
        """
        ps_path = shutil.which("powershell.exe")
        if ps_path:
            _logger.info("默认 shell 解析: powershell (%s)", ps_path)
            return "powershell"
        _logger.info("默认 shell 解析: powershell 不可用，回退至 cmd")
        return "cmd"

    def __init__(self, command, cols: int = 80, rows: int = 24, env=None, cwd=None,
                 shell: Optional[str] = None):
        use_shell = isinstance(command, str)
        # 显式继承环境变量，确保 PATH 等关键变量的传递
        # subprocess.Popen(env=None) 在部分平台/场景下行为有差异
        child_env = os.environ.copy() if env is None else env
        # 禁止进程崩溃弹出对话框（如 as.exe 的"应用程序错误"）
        # 使崩溃进程直接退出并返回 NTSTATUS 退出码
        # SetThreadErrorMode 和 SetErrorMode 同时调用确保子进程继承
        old_mode_thread = None
        old_mode_process = None
        if IS_WINDOWS:
            try:
                err_flags = (_SEM_NOGPFAULTERRORBOX | _SEM_FAILCRITICALERRORS
                             | _SEM_NOOPENFILEERRORBOX)
                prev_t = W.DWORD(0)
                if _SetThreadErrorMode(err_flags, ctypes.byref(prev_t)):
                    old_mode_thread = prev_t.value
                # SetErrorMode 是进程级设置，子进程会继承
                old_mode_process = K.SetErrorMode(err_flags)
            except Exception:
                pass
        try:
            startupinfo = None
            if IS_WINDOWS:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= _STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = _SW_HIDE
            if use_shell and IS_WINDOWS:
                # 处理 --shell 参数：选择指定的解释器
                # 默认优先使用 powershell，不可用时回退至 cmd.exe
                effective_shell = shell if shell else self._resolve_default_shell()
                shell_spec = self._SHELL_MAP.get(effective_shell)
                if shell_spec is None:
                    # cmd.exe：shell=True
                    _logger.info("Popen(shell=True) command=%r", command[:200])
                    self._proc = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        shell=True,
                        env=child_env,
                        cwd=cwd,
                        bufsize=0,
                        creationflags=_CREATE_NO_WINDOW,
                        startupinfo=startupinfo,
                    )
                else:
                    # 指定解释器（powershell/pwsh/bash）：构建命令行列表，shell=False
                    shell_exe, shell_arg = shell_spec
                    full_cmd = [shell_exe, shell_arg, command]
                    _logger.info("Popen(shell=%s) cmd=%r", effective_shell, full_cmd)
                    self._proc = subprocess.Popen(
                        full_cmd,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        shell=False,
                        env=child_env,
                        cwd=cwd,
                        bufsize=0,
                        creationflags=_CREATE_NO_WINDOW,
                        startupinfo=startupinfo,
                    )
            else:
                _logger.info("Popen(shell=%s) command=%r", use_shell, command)
                self._proc = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    shell=use_shell,
                    env=child_env,
                    cwd=cwd,
                    bufsize=0,
                    creationflags=_CREATE_NO_WINDOW if IS_WINDOWS else 0,
                    startupinfo=startupinfo,
                )
            _logger.info("Popen OK pid=%d", self._proc.pid)
        finally:
            # 恢复原始错误模式
            if IS_WINDOWS:
                try:
                    if old_mode_thread is not None:
                        _SetThreadErrorMode(old_mode_thread, None)
                    if old_mode_process is not None:
                        K.SetErrorMode(old_mode_process)
                except Exception:
                    pass
        self._child_pid = self._proc.pid

        # Job Object：追踪整个进程树，检测子/孙进程崩溃
        self._job = ProcessJob(name=f"subproc-{id(self)}") if IS_WINDOWS else None
        if self._job and self._proc.pid:
            try:
                PROCESS_ALL_ACCESS = 0x1F0FFF
                hproc = K.OpenProcess(PROCESS_ALL_ACCESS, False, self._proc.pid)
                _logger.info("OpenProcess(pid=%d) hproc=%s", self._proc.pid, hproc)
                if hproc:
                    self._job.assign(hproc)
                    _logger.info("Job assign OK")
                    _CloseHandle(hproc)
            except Exception as e:
                _logger.warning("Job assign failed: %s", e)
        # GUI 窗口检测器（基于 Job 进程树）
        self._gui_monitor = GuiWindowMonitor(job=self._job) if IS_WINDOWS else None

    def read(self, n: int = 65536) -> bytes:
        """读取子进程输出

        close() 会先关闭 stdout 管道，导致此处阻塞的 read() 立即返回 b""（EOF），
        从而安全解除 reader 线程阻塞。不依赖跨平台非阻塞 IO。

        Args:
            n: 最大读取字节数。

        Returns:
            读取到的字节数据。管道 EOF 时返回 b""。
        """
        try:
            if self._proc.stdout is None or self._proc.stdout.closed:
                return b""
            return self._proc.stdout.read(n)
        except (ValueError, OSError):
            # 管道在读取过程中被关闭（如 close() 在另一线程调用）
            return b""

    def drain(self, max_bytes: int = 65536) -> bytes:
        """排空管道缓冲区

        阻塞模式下无法实现真正的非阻塞排空，使用基类默认实现。
        由于 session._reader_loop 在每次 read() 后调用 drain()，
        而该 read() 已经读取了当前可用的大部分数据，drain 只会返回少量残余。

        Args:
            max_bytes: 单次读取的大小上限（本实现直接返回 b""）。

        Returns:
            当前实现返回 b""（阻塞模式下管道读取会等待新数据）。
        """
        return b""

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        self._proc.stdin.write(data)
        self._proc.stdin.flush()

    def fileno(self):
        return self._proc.stdout.fileno()

    def close(self):
        """关闭子进程管道并终止进程

        先关闭 stdout 管道，解除 reader 线程的阻塞式 read() 调用，
        再终止子进程。此顺序确保 reader 线程能及时退出。
        """
        # 先关闭 stdout 管道：解除 reader 线程中 read() 的阻塞
        try:
            if self._proc.stdout and not self._proc.stdout.closed:
                self._proc.stdout.close()
        except (OSError, AttributeError):
            pass
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(3)
            except Exception:
                self._proc.kill()
        if self._job:
            self._job.close()
        if self._gui_monitor:
            self._gui_monitor.close()

    def get_type(self) -> str:
        """返回 PTY 后端类型标识"""
        return "subprocess"

    def get_child_pid(self):
        return self._child_pid

    def get_exit_code(self) -> Optional[int]:
        """获取子进程退出码

        通过 subprocess.Popen.poll() 获取，非阻塞。

        Returns:
            退出码（int），若进程仍在运行则返回 None。
        """
        try:
            return self._proc.poll()
        except Exception:
            return None

    # ---- Job Object + 进程树查询 ----

    def get_process_list(self) -> List[int]:
        """获取 Job 进程树所有 PID"""
        if not self._job:
            return []
        return self._job.query_process_list()

    def poll_gui_windows(self) -> List[dict]:
        """轮询检测 Job 进程树中新增的 GUI 窗口"""
        if not self._gui_monitor:
            return []
        return [w.to_dict() for w in self._gui_monitor.poll()]

    def close_gui_window(self, hwnd: int) -> bool:
        """通过 WM_CLOSE 关闭指定 GUI 窗口"""
        if not self._gui_monitor:
            return False
        return self._gui_monitor.close_window(hwnd)

    def get_gui_windows(self) -> List[dict]:
        """获取已检测到的 GUI 窗口列表"""
        if not self._gui_monitor:
            return []
        return [w.to_dict() for w in self._gui_monitor.windows]

    def get_child_process_exit_code(self, pid: int) -> Optional[int]:
        """查询 Job 进程中某个 PID 的退出码（委托给 ProcessJob）"""
        if not self._job:
            return None
        return self._job.query_process_exit_code(pid)

    def get_job_notifications(self) -> list:
        """获取 Job Object 实时通知（IOCP 推送）"""
        if not self._job:
            return []
        return self._job.drain_notifications()
