"""WindowsPseudoTerminal — 基于 kernel32.CreatePseudoConsole API 的 PTY 实现"""

import logging
import subprocess
import ctypes
from ctypes import wintypes as W
from typing import Optional, List

from ..base import PseudoTerminal

_logger = logging.getLogger("pty-windows")
from .convars import (
    K,
    _CreatePseudoConsole,
    _ClosePseudoConsole,
    _ReadFile,
    _WriteFile,
    _CloseHandle,
    _GetExitCodeProcess,
    _PeekNamedPipe,
    _InitAttrList,
    _UpdateAttr,
    _DeleteAttrList,
    _CreateProcess,
    _HPCON,
    _COORD,
    _SIE,
    _PI,
)
from .error_msg import STILL_ACTIVE
from .job import ProcessJob
from .gui_monitor import GuiWindowMonitor, GuiWindowInfo


class WindowsPseudoTerminal(PseudoTerminal):
    """ConPTY — 基于 kernel32.CreatePseudoConsole API

    使用 CreatePseudoConsole + 双 CreatePipe 匿名管道。
    """

    def __init__(self, command, cols: int = 80, rows: int = 24, env=None, cwd=None):
        self._inR = W.HANDLE()
        self._inW = W.HANDLE()
        self._outR = W.HANDLE()
        self._outW = W.HANDLE()
        self._hpc = None
        self._ph = None
        self._child_pid = None
        self._job = ProcessJob(name=f"pty-{id(self)}")
        self._gui_monitor = GuiWindowMonitor(job=self._job)

        _logger.info("WindowsPseudoTerminal: creating pipes for cmd=%r", command)
        K.CreatePipe(ctypes.byref(self._inR), ctypes.byref(self._inW), None, 0)
        K.CreatePipe(ctypes.byref(self._outR), ctypes.byref(self._outW), None, 0)

        HANDLE_FLAG_INHERIT = 1
        K.SetHandleInformation(self._inR, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)
        K.SetHandleInformation(self._outW, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)

        self._hpc = _HPCON()
        hr = _CreatePseudoConsole(
            _COORD(cols, rows), self._inR, self._outW, 0,
            ctypes.byref(self._hpc),
        )
        if hr < 0:
            raise OSError(f"CreatePseudoConsole 失败 hr={hr:#x}")
        _logger.info("CreatePseudoConsole OK hr=%d", hr)

        cmdline = subprocess.list2cmdline(command)
        attr_size = ctypes.c_size_t(0)
        _InitAttrList(None, 1, 0, ctypes.byref(attr_size))
        buf = ctypes.create_string_buffer(attr_size.value)
        if not _InitAttrList(buf, 1, 0, ctypes.byref(attr_size)):
            raise OSError("InitAttrList 失败")
        if not _UpdateAttr(
            buf, 0, 0x00020016,
            ctypes.byref(self._hpc), ctypes.sizeof(_HPCON), None, None,
        ):
            _DeleteAttrList(buf)
            raise OSError("UpdateAttr 失败")
        si = _SIE()
        si.StartupInfo.cb = ctypes.sizeof(_SIE)
        si.StartupInfo.dwFlags = 0x00000100
        si.StartupInfo.hStdInput = self._inR
        si.StartupInfo.hStdOutput = self._outW
        si.StartupInfo.hStdError = self._outW
        si.lpAttributeList = ctypes.cast(buf, ctypes.c_void_p)
        _CREATE_UNICODE_ENVIRONMENT = 0x00080000
        _CREATE_NO_WINDOW = 0x08000000
        _EXTENDED_STARTUPINFO_PRESENT = 0x00000400
        pi = _PI()
        ok = _CreateProcess(
            None, cmdline, None, None, True,
            _CREATE_UNICODE_ENVIRONMENT | _CREATE_NO_WINDOW | _EXTENDED_STARTUPINFO_PRESENT,
            None, cwd,
            ctypes.byref(si.StartupInfo), ctypes.byref(pi),
        )
        _DeleteAttrList(buf)
        if not ok:
            err = ctypes.get_last_error()
            _logger.error("CreateProcessW 失败: err=%d", err)
            raise OSError(err, "CreateProcessW 失败")
        self._child_pid = pi.dwProcessId
        self._ph = pi.hProcess
        _logger.info("CreateProcessW OK pid=%d", self._child_pid)
        _CloseHandle(pi.hThread)
        # 将子进程分配到 Job Object
        self._job.assign(pi.hProcess)

        # CreatePseudoConsole 内部持有 self._inR 和 self._outW 的副本。
        # 关闭父进程中的副本，使子进程退出时管道写端全部关闭。
        # 同时关闭伪控制台，释放其内部的句柄引用。
        _CloseHandle(self._inR)
        self._inR = None
        _CloseHandle(self._outW)
        self._outW = None
        if self._hpc:
            _ClosePseudoConsole(self._hpc)
            self._hpc = None

    def read(self, n: int = 65536) -> bytes:
        buf = ctypes.create_string_buffer(n)
        br = W.DWORD(0)
        if not _ReadFile(self._outR, buf, n, ctypes.byref(br), None):
            err = ctypes.get_last_error()
            if err == 109:  # ERROR_BROKEN_PIPE
                _logger.debug("read: broken pipe (EOF)")
                return b""
            _logger.warning("read: ReadFile failed err=%d", err)
            return b""
        if br.value:
            _logger.debug("read: %d bytes", br.value)
        return buf.raw[:br.value]

    def drain(self, max_bytes: int = 65536) -> bytes:
        """排空管道输出缓冲区中当前所有就绪数据（基于 PeekNamedPipe 非阻塞检查）"""
        chunks = []
        total = 0
        while True:
            avail = W.DWORD(0)
            ok = _PeekNamedPipe(self._outR, None, 0, None, ctypes.byref(avail), None)
            if not ok or avail.value == 0:
                break
            n = min(avail.value, max_bytes)
            buf = ctypes.create_string_buffer(n)
            br = W.DWORD(0)
            if not _ReadFile(self._outR, buf, n, ctypes.byref(br), None):
                break
            if br.value == 0:
                break
            chunks.append(buf.raw[:br.value])
            total += br.value
        if total:
            _logger.debug("drain: %d total bytes", total)
        return b"".join(chunks)

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        _logger.debug("write: %d bytes", len(data))
        wr = W.DWORD(0)
        _WriteFile(self._inW, data, len(data), ctypes.byref(wr), None)

    def kill_tree(self):
        """强杀整个进程树：关闭 Job（KILL_ON_JOB_CLOSE）"""
        if self._job:
            try:
                self._job.close()
            except Exception:
                pass

    def close(self):
        """关闭伪终端并清理资源"""
        _logger.info("close: pid=%d", self._child_pid)
        # 1. 先关闭伪控制台（子进程的 ConPTY 写端关闭）
        if self._hpc:
            _ClosePseudoConsole(self._hpc)
            self._hpc = None
        # 2. 关闭进程句柄（不再需要查询退出码后即可关闭）
        if self._ph:
            _CloseHandle(self._ph)
            self._ph = None
        # 3. 关闭 Job Object（KILL_ON_JOB_CLOSE 终止所有进程树）
        #    → 子进程终止 → 管道写端关闭 → reader 的 ReadFile 收到 EOF
        self._job.close()
        # 4. 现在管道写端已关闭，reader 的 ReadFile 应已收到 EOF 返回
        #    可以安全关闭读端管道句柄
        for h in (self._inW, self._outR, self._inR, self._outW):
            if h:
                _CloseHandle(h)
        self._inW = self._outR = self._inR = self._outW = None
        # 5. 清理 GUI 监控
        self._gui_monitor.close()

    def get_type(self) -> str:
        """返回 PTY 后端类型标识"""
        return "win-conpty"

    def get_child_pid(self):
        return self._child_pid

    def get_exit_code(self) -> Optional[int]:
        """获取子进程退出码

        通过 GetExitCodeProcess 获取子进程的退出码。

        Returns:
            退出码（int），若进程仍在运行则返回 None。
        """
        if not self._ph:
            return None
        try:
            code = W.DWORD(0)
            if not _GetExitCodeProcess(self._ph, ctypes.byref(code)):
                return None
            if code.value == STILL_ACTIVE:
                return None
            return code.value
        except Exception:
            return None

    # ---- Job Object + GUI 窗口检测 ----

    def get_process_list(self) -> List[int]:
        """获取进程树所有进程的 PID 列表

        通过 QueryInformationJobObject 查询 Job 内所有进程。

        Returns:
            PID 列表。
        """
        return self._job.query_process_list()

    def get_child_process_exit_code(self, pid: int) -> Optional[int]:
        """查询 Job 进程中某个 PID 的退出码"""
        return self._job.query_process_exit_code(pid)

    def get_job_notifications(self) -> list:
        """获取 Job Object 实时通知"""
        if not self._job:
            return []
        return self._job.drain_notifications()

    def get_gui_windows(self) -> List[dict]:
        """获取已检测到的 GUI 窗口列表

        Returns:
            窗口信息字典列表（hwnd, pid, title, class_name）。
        """
        return [w.to_dict() for w in self._gui_monitor.windows]

    def poll_gui_windows(self) -> List[dict]:
        """轮询检测新增 GUI 窗口

        Returns:
            本轮新增的窗口信息字典列表。
        """
        return [w.to_dict() for w in self._gui_monitor.poll()]

    def close_gui_window(self, hwnd: int) -> bool:
        """关闭指定 GUI 窗口

        Args:
            hwnd: 窗口句柄。

        Returns:
            True 表示 WM_CLOSE 已发送。
        """
        return self._gui_monitor.close_window(hwnd)
