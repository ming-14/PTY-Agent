"""SubprocessBackend — 基于 subprocess 管道的子进程后端

纯 stdin/stdout/stderr 管道模式，不涉及任何伪终端概念。
"""

import ctypes
import os
import shutil
import subprocess
import threading
import logging
from typing import Optional, List
from ctypes import wintypes as W

from .base import Backend, ProcessEvent
from ..config import IS_WINDOWS, READ_SIZE

if IS_WINDOWS:
    from .windows.job import ProcessJob
    from .windows.gui_monitor import GuiWindowMonitor
    from .windows.convars import K, _SetThreadErrorMode

# ── Windows 错误模式常量（禁止子进程弹出崩溃对话框）──
_SEM_FAILCRITICALERRORS     = 0x0001   # 禁止 critical-error-handler 消息框
_SEM_NOGPFAULTERRORBOX      = 0x0002   # 禁止一般保护错误消息框
_SEM_NOOPENFILEERRORBOX     = 0x8000   # 禁止文件打开失败消息框
_CREATE_NO_WINDOW           = 0x08000000  # 禁止为控制台程序创建可见窗口
_STARTF_USESHOWWINDOW       = 0x00000001
_SW_HIDE                    = 0

_logger = logging.getLogger("backend-subprocess")

# ── 进程级错误模式（全进程只设一次）──
_ERR_MODE_LOCK = threading.Lock()
_ERR_MODE_DONE = False


def _silence_process_error_dialogs() -> None:
    """进程级屏蔽崩溃对话框（新建的子进程会继承该设置）

    SetErrorMode 是**进程全局**的：若放在"每次创建后端"的路径上做
    保存/恢复，并发启动多个 Session 时两个线程会交错 —— 后恢复的一方拿到
    的是已被对方改过的"旧值"，错误模式可能永久停在被改后的状态。
    因此这里只做一次、永不回滚。线程级设置（SetThreadErrorMode）仍按
    每次创建单独保存/恢复，线程之间互不影响。
    """
    global _ERR_MODE_DONE
    if _ERR_MODE_DONE:
        return
    with _ERR_MODE_LOCK:
        if _ERR_MODE_DONE:
            return
        try:
            K.SetErrorMode(_SEM_NOGPFAULTERRORBOX | _SEM_FAILCRITICALERRORS
                           | _SEM_NOOPENFILEERRORBOX)
            _ERR_MODE_DONE = True
        except Exception:
            pass


def detect_available_shells() -> dict:
    """检测当前环境可用的 shell 解释器（按平台映射逐项检测）

    Returns:
        字典，键为 shell 名称，值为可执行文件路径（不可用则为 None）。
        例如（Windows）: {"cmd": "C:\\Windows\\System32\\cmd.exe",
                         "powershell": None, ...}
    """
    # 映射值为 None 的解释器由 shell=True 承担，检测其隐含的可执行文件
    shell_true_exe = "cmd.exe" if IS_WINDOWS else "sh"
    result = {}
    for name, spec in SubprocessBackend.shell_map().items():
        exe = shell_true_exe if spec is None else spec[0]
        result[name] = shutil.which(exe) or (exe if spec is None else None)
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


class SubprocessBackend(Backend):
    """subprocess 管道模式

    使用 subprocess.Popen 的 stdin/stdout/stderr 管道进行交互。
    纯管道进程，不涉及伪终端。默认优先使用 powershell，不可用时
    回退至 cmd.exe，可通过 shell 参数切换解释器。
    """

    # 可选解释器映射：解释器名 → [可执行文件, 命令参数]；None 表示交给 shell=True。
    # Windows：cmd → shell=True（即 cmd.exe）
    _WINDOWS_SHELL_MAP = {
        "cmd":        None,                         # subprocess shell=True → cmd.exe
        "powershell": ["powershell.exe", "-Command"],
        "pwsh":       ["pwsh.exe",      "-Command"],
        "bash":       ["bash.exe",      "-c"],
    }
    # POSIX：无 cmd 概念，默认 shell=True 即 /bin/sh；显式 sh 同义
    _POSIX_SHELL_MAP = {
        "sh":         None,                         # subprocess shell=True → /bin/sh
        "bash":       ["bash",         "-c"],
        "pwsh":       ["pwsh",         "-Command"],
        "powershell": ["powershell",   "-Command"],
    }

    @classmethod
    def shell_map(cls) -> dict:
        """当前平台支持的 解释器名 → spec 映射"""
        return cls._WINDOWS_SHELL_MAP if IS_WINDOWS else cls._POSIX_SHELL_MAP

    @classmethod
    def shell_choices(cls) -> tuple:
        """当前平台支持的 `--shell` 取值"""
        return tuple(cls.shell_map().keys())

    @classmethod
    def _resolve_shell_spec(cls, shell: Optional[str]):
        """把 --shell 取值解析为 [可执行文件, 参数]；None 表示走 shell=True

        Args:
            shell: 用户指定的解释器名，None 表示未指定（用平台默认）。

        Returns:
            [可执行文件, 命令参数] 或 None。

        Raises:
            RuntimeError: 指定了当前平台不支持的解释器。
        """
        shell_map = cls.shell_map()
        if shell:
            if shell not in shell_map:
                raise RuntimeError(
                    f"当前平台不支持 shell '{shell}'"
                    f"（可用: {', '.join(shell_map)}）"
                )
            return shell_map[shell]
        if IS_WINDOWS:
            # 未指定：优先 powershell，不可用时回退 cmd（shell=True）
            return shell_map[cls._resolve_default_shell()]
        return None

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
        # 禁止子进程崩溃时弹出对话框（如 as.exe 的"应用程序错误"），使崩溃
        # 进程直接退出并返回 NTSTATUS 退出码。CreateProcess 由本线程发起，
        # 子进程继承本线程的错误模式，故线程级设置用完即恢复；进程级设置
        # 一次性完成（见 _silence_process_error_dialogs 的说明）。
        old_mode_thread = None
        if IS_WINDOWS:
            try:
                err_flags = (_SEM_NOGPFAULTERRORBOX | _SEM_FAILCRITICALERRORS
                             | _SEM_NOOPENFILEERRORBOX)
                prev_t = W.DWORD(0)
                if _SetThreadErrorMode(err_flags, ctypes.byref(prev_t)):
                    old_mode_thread = prev_t.value
                _silence_process_error_dialogs()
            except Exception:
                pass
        try:
            startupinfo = None
            if IS_WINDOWS:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= _STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = _SW_HIDE

            # 三种情形（shell=True / 指定解释器列表 / 列表命令）共用一次 Popen 调用，
            # 避免此前"Windows 分支 + 通用分支"两份近似代码各说各话
            popen_kwargs = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "env": child_env,
                "cwd": cwd,
                "bufsize": 0,
                "startupinfo": startupinfo,
            }
            if IS_WINDOWS:
                popen_kwargs["creationflags"] = _CREATE_NO_WINDOW

            if not use_shell:
                # 列表命令：直接执行，不经过任何 shell
                target = command
                popen_kwargs["shell"] = False
            else:
                shell_spec = self._resolve_shell_spec(shell)
                if shell_spec is None:
                    # cmd（Windows）/ /bin/sh（POSIX）：交给 shell=True
                    target = command
                    popen_kwargs["shell"] = True
                    _logger.info("Popen(shell=True) command=%r", command[:200])
                else:
                    # 指定解释器：构建命令行列表，shell=False
                    shell_exe, shell_arg = shell_spec
                    target = [shell_exe, shell_arg, command]
                    popen_kwargs["shell"] = False
                    _logger.info("Popen(shell=%r) cmd=%r", shell, target)

            self._proc = subprocess.Popen(target, **popen_kwargs)
            _logger.info("Popen OK pid=%d", self._proc.pid)
        finally:
            # 只恢复本线程的错误模式；进程级设置是一次性的，不在此回滚
            if IS_WINDOWS and old_mode_thread is not None:
                try:
                    _SetThreadErrorMode(old_mode_thread, None)
                except Exception:
                    pass
        self._child_pid = self._proc.pid

        # Job Object：追踪整个进程树，检测子/孙进程崩溃
        self._job = ProcessJob(name=f"subproc-{id(self)}") if IS_WINDOWS else None
        if self._job:
            self._assign_to_job()
        # GUI 窗口检测器（基于 Job 进程树）
        self._gui_monitor = GuiWindowMonitor(job=self._job) if IS_WINDOWS else None

    def _assign_to_job(self):
        """把子进程放进 Job Object（KILL_ON_JOB_CLOSE + IOCP 通知）

        必须使用 CreateProcess 返回的原生句柄（Popen._handle），**不能**按 PID
        再 OpenProcess：子进程可能已经退出且 PID 被系统复用，那样会把一个无关
        进程拉进带 KILL_ON_JOB_CLOSE 的 Job —— Job 句柄关闭时就会杀掉那个无辜
        进程（轻则误伤用户程序，重则连带宿主/CI runner 一起没了）。
        句柄由 Popen 自己关闭，这里绝不 CloseHandle。
        """
        handle = getattr(self._proc, "_handle", None)
        # MagicMock 之类的替身（测试里 Popen 被 patch）不是真句柄，直接跳过
        if not isinstance(handle, int) or handle <= 0:
            _logger.debug("Job assign 跳过：无可用原生句柄")
            return
        try:
            if self._job.assign(handle, expected_pid=self._proc.pid):
                _logger.info("Job assign OK pid=%d", self._proc.pid)
            else:
                _logger.warning("Job assign 失败 pid=%d", self._proc.pid)
        except Exception as e:
            _logger.warning("Job assign 异常 pid=%d: %s", self._proc.pid, e)

    def read(self, n: int = READ_SIZE) -> bytes:
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

    def drain(self, max_bytes: int = READ_SIZE) -> bytes:
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
            data = data.encode("utf-8")
        self._proc.stdin.write(data)
        self._proc.stdin.flush()

    def fileno(self):
        return self._proc.stdout.fileno()

    def remove_tree(self):
        """强杀整个进程树：先关闭 Job（KILL_ON_JOB_CLOSE），再强杀子进程"""
        if IS_WINDOWS and self._job:
            try:
                self._job.close()
                _logger.info("remove_tree: Job closed, KILL_ON_JOB_CLOSE triggered")
                return
            except Exception as e:
                _logger.warning("remove_tree: Job close failed: %s", e)
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    def close(self):
        """关闭子进程管道并终止进程

        先终止子进程（关闭管道写端），让 reader 线程的阻塞式 read()
        因 EOF 返回并自然退出，再关闭读端。避免在 reader 线程持有
        BufferedReader 内部锁时调用 stdout.close() 导致死锁
        （close 的 flush 会等 read 释放锁，read 在等数据，循环等待）。
        """
        # 1. 先终止子进程 → 写端关闭 → reader 的 read() 收到 EOF 退出
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(3)
            except Exception:
                self._proc.kill()
                try:
                    self._proc.wait(1)
                except Exception:
                    pass
        # 2. 关闭 stdin（避免子进程卡在等输入，也防止后续 write 报错）
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except (OSError, AttributeError):
            pass
        # 3. 关闭 stdout 读端（此时 reader 应已因 EOF 退出，不会与 read() 抢锁）
        try:
            if self._proc.stdout and not self._proc.stdout.closed:
                self._proc.stdout.close()
        except (OSError, AttributeError):
            pass
        # 4. 关闭 Job（KILL_ON_JOB_CLOSE 终止残留子/孙进程）
        if self._job:
            self._job.close()
        if self._gui_monitor:
            self._gui_monitor.close()

    def get_type(self) -> str:
        """返回后端类型标识"""
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
        """轮询检测 Job 进程树中新增的 GUI 窗口（含取走事件路径结果）"""
        if not self._gui_monitor:
            return []
        return [w.to_dict() for w in self._gui_monitor.poll()]

    def take_pending_gui_windows(self) -> List[dict]:
        """取走 WinEvent hook 已检出的窗口（事件驱动，无节流）"""
        if not self._gui_monitor:
            return []
        return [w.to_dict() for w in self._gui_monitor.take_pending()]

    def set_gui_listener(self, callback) -> None:
        """注册新窗口回调，用于即时唤醒等待循环"""
        if not self._gui_monitor:
            return None
        self._gui_monitor.set_listener(callback)
        return None

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

    def get_job_notifications(self) -> List[ProcessEvent]:
        """获取 Job Object 实时通知（IOCP 推送）"""
        if not self._job:
            return []
        return self._job.drain_notifications()

    def wait_for_job_notification(self, timeout: float) -> bool:
        """等待新通知到达（事件驱动，非忙等）"""
        if not self._job:
            return False
        return self._job.wait_notification(timeout)
