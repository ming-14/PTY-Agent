"""ConDrvPseudoTerminal — 仿 Windows Terminal 直连 ConDrv + conhost.exe

**已禁用**：当前 _CONDRV_OK = False，整个后端无法通过正常路径实例化。
保留源码供后续调试/恢复使用（需补全 IOCTL 握手序列）。

若后续需要重新开启，需参考 Windows Terminal 源码实现完整的
IOCTL 握手序列，而非仅依赖 HANDLE_LIST + CreatePipe。

管道架构（对齐 Windows Terminal winconpty.cpp）:
  - _inR / _inW  : 输入管道（conhost 读 _inR，我们写 _inW）
  - _outR / _outW: 输出管道（conhost 写 _outW，我们读 _outR）
  - sig_r / sig_w: 信号管道（conhost 读 sig_r，我们写 sig_w）
"""

import os
import ctypes
import subprocess
from ctypes import wintypes as W
from typing import Optional, List

from ..base import PseudoTerminal
from .convars import (
    K,
    _CONDRV_OK,
    _CreateNamedPipeW,
    _CreateFileW,
    _ConnectNamedPipe,
    _ClosePseudoConsole,
    _ReadFile,
    _WriteFile,
    _GetOverlappedResult,
    _WaitMultiple,
    _CloseHandle,
    _CreateEventW,
    _ResetEvent,
    _InitAttrList,
    _UpdateAttr,
    _DeleteAttrList,
    _CancelIoEx,
    _CreateProcess,
    _CreateProcessAsUserW,
    _GetExitCodeProcess,
    _NtOpenFile,
    _HPCON,
    _UNICODE_STRING,
    _OBJECT_ATTRIBUTES,
    _IO_STATUS_BLOCK,
    _COORD,
    _OVERLAPPED,
    _SIE,
    _PI,
    _PSEUDO_CONSOLE,
)
from .error_msg import STILL_ACTIVE
from .job import ProcessJob
from .gui_monitor import GuiWindowMonitor, GuiWindowInfo

_SIGNAL_CMD_CLOSE = b"\x00\x00\x00\x00"


class ConDrvPseudoTerminal(PseudoTerminal):
    """直接通过 ConDrv + conhost.exe 创建伪终端

    完全绕过 kernel32.CreatePseudoConsole，仿 Windows Terminal 做法:
      1. 双独立 I/O 管道（输入/输出分离，对齐 Termial）
      2. NtOpenFile("\\Device\\ConDrv\\Server")
      3. 创建 Reference Handle
      4. 创建 Signal Pipe
      5. 启动 conhost.exe --headless（HANDLE_LIST 传递 server/inR/outW/sigR）
      6. 子进程通过 HPCON 附着到本控制台
    """

    def __init__(self, command, cols: int = 80, rows: int = 24, env=None):
        if not _CONDRV_OK:
            raise OSError("ConDrv 驱动不可用（需要管理员权限或系统不支持）")

        self._inR:  Optional[int] = None   # conhost stdin 读端
        self._inW:  Optional[int] = None   # 父进程写端
        self._outR: Optional[int] = None   # 父进程读端
        self._outW: Optional[int] = None   # conhost stdout/stderr 写端
        self._hpc = None
        self._ph = None
        self._child_pid = None
        self._signal = None
        self._ref_h = None
        self._conhost_proc = None
        self._evt = None
        self._buf = ctypes.create_string_buffer(131072)
        self._pending = False
        self._ov = _OVERLAPPED()
        self._job = ProcessJob(name=f"pty-con-{id(self)}")
        self._gui_monitor = GuiWindowMonitor(job=self._job)

        # ── 1. 双独立 I/O 管道（对齐 Terminal：创建即 inheritable）──
        # 关键：conhost 通过 GetStdHandle 读 I/O pipe，需要 handle 可继承
        # CreatePipe 默认 non-inheritable → 必须用 SECURITY_ATTRIBUTES 设 bInheritHandle=TRUE
        class _SECURITY_ATTRIBUTES(ctypes.Structure):
            _fields_ = [
                ("nLength",              W.DWORD),
                ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle",       W.BOOL),
            ]
        sa = _SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(_SECURITY_ATTRIBUTES)
        sa.bInheritHandle = True
        sa.lpSecurityDescriptor = None

        inR, inW = W.HANDLE(), W.HANDLE()
        outR, outW = W.HANDLE(), W.HANDLE()
        if not K.CreatePipe(ctypes.byref(inR), ctypes.byref(inW), ctypes.byref(sa), 0):
            raise OSError("CreatePipe (in) 失败")
        if not K.CreatePipe(ctypes.byref(outR), ctypes.byref(outW), ctypes.byref(sa), 0):
            _CloseHandle(inR); _CloseHandle(inW)
            raise OSError("CreatePipe (out) 失败")
        self._inR, self._inW, self._outR, self._outW = inR, inW, outR, outW

        # ── 2. 打开 ConDrv Server ──
        server_h = W.HANDLE()
        sname = "\\Device\\ConDrv\\Server"
        sbuf = ctypes.create_unicode_buffer(sname)
        us = _UNICODE_STRING(
            len(sname) * 2, (len(sname) + 1) * 2,
            ctypes.cast(sbuf, W.LPWSTR),
        )
        oa = _OBJECT_ATTRIBUTES(
            ctypes.sizeof(_OBJECT_ATTRIBUTES),
            None, ctypes.pointer(us), 0x42, None, None,
        )
        iosb = _IO_STATUS_BLOCK()
        st = _NtOpenFile(
            ctypes.byref(server_h), 0x10000000,
            ctypes.byref(oa), ctypes.byref(iosb), 7, 0,
        )
        if st != 0:
            self._cleanup_handles(inR, inW, outR, outW)
            raise OSError(f"NtOpenFile ConDrv 失败: 0x{st:08x}")

        # ── 3. Reference Handle ──
        ref_h = W.HANDLE()
        rname = "\\Reference"
        rbuf = ctypes.create_unicode_buffer(rname)
        us2 = _UNICODE_STRING(
            len(rname) * 2, (len(rname) + 1) * 2,
            ctypes.cast(rbuf, W.LPWSTR),
        )
        oa2 = _OBJECT_ATTRIBUTES(
            ctypes.sizeof(_OBJECT_ATTRIBUTES), server_h,
            ctypes.pointer(us2), 0x42, None, None,
        )
        st2 = _NtOpenFile(
            ctypes.byref(ref_h), 0xC0100000,
            ctypes.byref(oa2), ctypes.byref(iosb), 7, 0x20,
        )
        if st2 != 0:
            _CloseHandle(server_h)
            self._cleanup_handles(inR, inW, outR, outW)
            raise OSError(f"Reference 失败: 0x{st2:08x}")

        # ── 4. Signal Pipe ──
        sig_r, sig_w = W.HANDLE(), W.HANDLE()
        K.CreatePipe(ctypes.byref(sig_r), ctypes.byref(sig_w), None, 0)
        K.SetHandleInformation.restype = W.BOOL
        K.SetHandleInformation.argtypes = [W.HANDLE, W.DWORD, W.DWORD]
        K.SetHandleInformation(sig_r, 1, 1)

        # ── 5. 启动 conhost.exe（对齐 Terminal: 双管道 + HANDLE_LIST）──
        conhost = "\\\\?\\" + os.path.join(
            os.environ.get("SystemRoot", "C:\\Windows"),
            "System32", "conhost.exe",
        )
        cmd = (
            f'"{conhost}" --headless --width {cols} --height {rows}'
            f' --signal 0x{sig_r.value:x} --server 0x{server_h.value:x}'
        )

        hlist_size = ctypes.c_size_t(0)
        _InitAttrList(None, 1, 0, ctypes.byref(hlist_size))
        hlist_buf = ctypes.create_string_buffer(hlist_size.value)
        if not _InitAttrList(hlist_buf, 1, 0, ctypes.byref(hlist_size)):
            raise OSError("InitAttrList 失败 (conhost)")

        # HANDLE_LIST 对齐 Terminal: [serverHandle, hInput, hOutput, signalPipe]
        inh = (W.HANDLE * 4)(server_h, inR, outW, sig_r)
        if not _UpdateAttr(
            hlist_buf, 0, 0x00020002,
            ctypes.byref(inh), ctypes.sizeof(inh), None, None,
        ):
            _DeleteAttrList(hlist_buf)
            raise OSError("UpdateAttr HANDLE_LIST 失败")

        sie = _SIE()
        sie.StartupInfo.cb = ctypes.sizeof(_SIE)
        sie.StartupInfo.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
        sie.StartupInfo.hStdInput = inR       # conhost 从输入管道读
        sie.StartupInfo.hStdOutput = outW     # conhost 写输出管道
        sie.StartupInfo.hStdError = outW      # stderr 也走输出管道
        sie.lpAttributeList = ctypes.cast(hlist_buf, ctypes.c_void_p)

        pi = _PI()
        ok = _CreateProcessAsUserW(
            None,                    # hToken = NULL（本地用户）
            conhost,                 # lpApplicationName — 对齐 Terminal
            cmd, None, None, True,
            0x00000400,              # EXTENDED_STARTUPINFO_PRESENT
            None, None,
            ctypes.byref(sie.StartupInfo), ctypes.byref(pi),
        )
        _DeleteAttrList(hlist_buf)
        if not ok:
            _CloseHandle(server_h)
            _CloseHandle(ref_h)
            self._cleanup_handles(inR, inW, outR, outW, sig_r, sig_w)
            raise OSError(ctypes.get_last_error(), "conhost.exe 启动失败")

        _CloseHandle(pi.hThread)
        _CloseHandle(pi.hThread)
        self._conhost_proc = pi.hProcess
        self._ref_h = ref_h
        self._signal = sig_w
        # 关闭父进程中已继承给 conhost 的句柄副本
        for h in (server_h, inR, outW, sig_r):
            _CloseHandle(h)
        self._inR = None   # conhost 持有
        self._outW = None  # conhost 持有

        # ── 6. 创建伪 HPCON ──
        self._pc = _PSEUDO_CONSOLE()
        self._pc.hSignal = sig_w
        self._pc.hPtyReference = ref_h
        self._pc.hConPtyProcess = pi.hProcess
        self._hpc = ctypes.cast(ctypes.pointer(self._pc), _HPCON)

        # ── 7. 启动子进程 ──
        self._start_child(command, env)

        # ── Overlapped I/O（从 _outR 读取）──
        self._evt = _CreateEventW(None, False, False, None)
        self._ov.hEvent = self._evt

    @staticmethod
    def _cleanup_handles(*handles):
        """安全关闭多个句柄，忽略 None 和关闭异常"""
        for h in handles:
            if h is not None:
                try:
                    _CloseHandle(h)
                except Exception:
                    pass

    def _start_child(self, command, env):
        """启动与 HPCON 绑定的子进程"""
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
        si.lpAttributeList = ctypes.cast(buf, ctypes.c_void_p)
        pi = _PI()

        env_block = None
        if isinstance(env, dict):
            m = os.environ.copy()
            m.update(env)
            env_block = ctypes.create_unicode_buffer(
                "\0".join(f"{k}={v}" for k, v in m.items()) + "\0\0",
            )
        ok = _CreateProcess(
            None, cmdline, None, None, True,
            0x00080000 | 0x00000400, env_block, None,
            ctypes.byref(si.StartupInfo), ctypes.byref(pi),
        )
        _DeleteAttrList(buf)
        if not ok:
            raise OSError(ctypes.get_last_error(), "CreateProcessW 失败")
        self._child_pid = pi.dwProcessId
        self._ph = pi.hProcess
        _CloseHandle(pi.hThread)
        self._job.assign(pi.hProcess)

    def read(self, n: int = 65536) -> bytes:
        """从输出管道读取 conhost 输出"""
        if not self._outR:
            return b""
        if not self._pending:
            self._pending = True
            _ResetEvent(self._evt)
            self._ov.Offset = 0
            self._ov.OffsetHigh = 0
            ok = _ReadFile(
                self._outR, self._buf, min(n, len(self._buf)),
                None, ctypes.byref(self._ov),
            )
            if ok:
                br = W.DWORD(0)
                _GetOverlappedResult(
                    self._outR, ctypes.byref(self._ov), ctypes.byref(br), False,
                )
                self._pending = False
                return self._buf.raw[:br.value] if br.value else b""
            err = ctypes.get_last_error()
            if err == 109:  # ERROR_BROKEN_PIPE
                self._pending = False
                return b""
            if err != 997:  # ERROR_IO_PENDING
                self._pending = False
                raise OSError(err, "ReadFile 失败")
        # 等待数据事件或进程退出（任一触发都尝试读取已缓存数据）
        hs = (W.HANDLE * 2)(self._evt, self._ph)
        r = _WaitMultiple(2, hs, False, 0xFFFFFFFF)
        self._pending = False
        br = W.DWORD(0)
        ok = _GetOverlappedResult(
            self._outR, ctypes.byref(self._ov), ctypes.byref(br), False,
        )
        data = self._buf.raw[:br.value] if ok and br.value else b""
        return data

    def write(self, data):
        """写入输入管道（对齐 Terminal：写到 _inW，conhost 从 _inR 读）"""
        if isinstance(data, str):
            data = data.encode()
        if not self._inW:
            raise OSError("输入管道已关闭")
        wr = W.DWORD(0)
        if not _WriteFile(self._inW, data, len(data), ctypes.byref(wr), None):
            raise OSError(ctypes.get_last_error(), "WriteFile 失败")

    def close(self):
        """关闭所有句柄并清理资源"""
        if self._hpc:
            _ClosePseudoConsole(self._hpc)
            self._hpc = None
        # 关闭我们持有的管道端
        for h in (self._inW, self._outR, self._inR, self._outW):
            if h is not None:
                try:
                    _CancelIoEx(h, None)
                except Exception:
                    pass
                _CloseHandle(h)
        self._inW = self._outR = self._inR = self._outW = None
        self._inR_close = self._outW_close = None
        for h in (self._evt, self._ph, self._signal, self._ref_h, self._conhost_proc):
            if h:
                _CloseHandle(h)
        self._evt = self._ph = self._signal = self._ref_h = self._conhost_proc = None
        self._job.close()
        self._gui_monitor.close()

    def get_type(self) -> str:
        """返回 PTY 后端类型标识"""
        return "win-condrv"

    def get_child_pid(self):
        return self._child_pid

    def get_exit_code(self) -> Optional[int]:
        """获取子进程退出码

        通过 GetExitCodeProcess 获取子进程的退出码。
        注意：子进程在 conhost.exe 内部运行，退出码通过进程句柄获取。

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
        """获取进程树所有进程的 PID 列表"""
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
        """获取已检测到的 GUI 窗口列表"""
        return [w.to_dict() for w in self._gui_monitor.windows]

    def poll_gui_windows(self) -> List[dict]:
        """轮询检测新增 GUI 窗口"""
        return [w.to_dict() for w in self._gui_monitor.poll()]

    def close_gui_window(self, hwnd: int) -> bool:
        """关闭指定 GUI 窗口"""
        return self._gui_monitor.close_window(hwnd)
