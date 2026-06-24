"""ConDrvPseudoTerminal — 完全复刻 Windows Terminal winconpty.cpp 方案

通过 NtOpenFile("\\Device\\ConDrv\\Server") + conhost.exe --headless 创建伪终端，
流程与 Windows Terminal src/winconpty/winconpty.cpp _CreatePseudoConsole 完全一致：

  1. CreateServerHandle → NtOpenFile("\\Device\\ConDrv\\Server", GENERIC_ALL, Inheritable=TRUE)
  2. CreateClientHandle → NtOpenFile("\\Reference", parent=serverHandle, Inheritable=FALSE)
  3. CreatePipe → 信号管道（sa.bInheritHandle=FALSE, conhost 侧单独 SetHandleInformation INHERIT）
  4. 构建 conhost.exe --headless --width X --height Y --signal 0x<sigR> --server 0x<serverH>
  5. HANDLE_LIST = [serverHandle, hInput, hOutput, signalPipeConhostSide]
  6. CreateProcessAsUserW(conhost, ..., EXTENDED_STARTUPINFO_PRESENT)
  7. 子进程通过 PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE (HPCON) 附着

管道架构（对齐 winconpty.cpp）:
  - _inW  : 父进程写端（我们写 VT 输入）
  - _outR : 父进程读端（我们读 VT 输出）
  - sig_w : 信号管道写端（resize/showhide/clear）
  - hPtyReference : 引用句柄（保持 conhost 存活）
  - hConPtyProcess : conhost 进程句柄
"""

import os
import ctypes
import subprocess
import logging
from ctypes import wintypes as W
from typing import Optional, List

from ..base import PseudoTerminal
from .convars import (
    K,
    _CONDRV_OK,
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
    _NtSetSystemInformation,
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

_logger = logging.getLogger("pty-condrv")

_OBJ_CASE_INSENSITIVE = 0x00000040
_OBJ_INHERIT = 0x00000002
_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
_GENERIC_ALL = 0x10000000
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_SYNCHRONIZE = 0x00100000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_HANDLE_FLAG_INHERIT = 0x00000001
_EXTENDED_STARTUPINFO_PRESENT = 0x00000400
_CREATE_UNICODE_ENVIRONMENT = 0x00080000
_CREATE_NO_WINDOW = 0x08000000
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_PTY_SIGNAL_RESIZE_WINDOW = 8


def _nt_create_handle(handle, device_name, desired_access, parent, inheritable, open_options):
    """对齐 DeviceHandle::_CreateHandle — NtOpenFile 封装"""
    flags = _OBJ_CASE_INSENSITIVE
    if inheritable:
        flags |= _OBJ_INHERIT

    name_buf = ctypes.create_unicode_buffer(device_name)
    us = _UNICODE_STRING(
        len(device_name) * 2,
        (len(device_name) + 1) * 2,
        ctypes.cast(name_buf, W.LPWSTR),
    )
    oa = _OBJECT_ATTRIBUTES(
        ctypes.sizeof(_OBJECT_ATTRIBUTES),
        parent,
        ctypes.pointer(us),
        flags,
        None,
        None,
    )
    iosb = _IO_STATUS_BLOCK()
    return _NtOpenFile(
        ctypes.byref(handle),
        desired_access,
        ctypes.byref(oa),
        ctypes.byref(iosb),
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        open_options,
    )


def _create_server_handle(handle, inheritable=True):
    """对齐 DeviceHandle::CreateServerHandle

    NtOpenFile("\\Device\\ConDrv\\Server", GENERIC_ALL, Inheritable, OpenOptions=0)
    """
    return _nt_create_handle(
        handle,
        "\\Device\\ConDrv\\Server",
        _GENERIC_ALL,
        None,
        inheritable,
        0,
    )


def _create_client_handle(handle, server_handle, name, inheritable=False):
    """对齐 DeviceHandle::CreateClientHandle

    NtOpenFile(name, GENERIC_READ|GENERIC_WRITE|SYNCHRONIZE, parent=serverHandle,
               Inheritable, OpenOptions=FILE_SYNCHRONOUS_IO_NONALERT)
    """
    return _nt_create_handle(
        handle,
        name,
        _GENERIC_READ | _GENERIC_WRITE | _SYNCHRONIZE,
        server_handle,
        inheritable,
        _FILE_SYNCHRONOUS_IO_NONALERT,
    )


def _ensure_driver_is_loaded():
    """对齐 winconpty::_EnsureDriverIsLoaded

    通过 NtSetSystemInformation(SystemConsoleInformation=132) 加载 ConDrv 驱动
    """
    info = W.ULONG(1)
    _NtSetSystemInformation(132, ctypes.byref(info), ctypes.sizeof(W.ULONG))


class ConDrvPseudoTerminal(PseudoTerminal):
    """完全复刻 Windows Terminal winconpty.cpp _CreatePseudoConsole 的 ConDrv 直连方案

    流程与 winconpty.cpp:119-278 完全一致：
      1. CreateServerHandle (Inheritable=TRUE)
      2. CreateClientHandle("\\Reference", Inheritable=FALSE)
      3. CreatePipe 信号管道 (sa.bInheritHandle=FALSE, conhost 侧 SetHandleInformation INHERIT)
      4. 构造 conhost.exe --headless 命令行
      5. HANDLE_LIST = [serverHandle, hInput, hOutput, signalPipeConhostSide]
      6. CreateProcessAsUserW 启动 conhost
      7. 子进程通过 PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE 附着
    """

    def __init__(self, command, cols: int = 80, rows: int = 24, env=None):
        if not _CONDRV_OK:
            raise OSError("ConDrv 驱动不可用（需要管理员权限或系统不支持）")

        self._inW: Optional[int] = None
        self._outR: Optional[int] = None
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

        # ── 1. I/O 管道（对齐 winconpty.cpp: hInput/hOutput 由调用方传入）──
        # winconpty.cpp 中 hInput/hOutput 由 ConptyCreatePseudoConsole 的调用方提供，
        # 这里我们自行创建双管道。Terminal 使用 Overlapped Named Pipe，
        # 我们使用同步匿名管道（足够用，conhost 侧通过 hStdInput/hStdOutput 访问）。
        inR, inW = W.HANDLE(), W.HANDLE()
        outR, outW = W.HANDLE(), W.HANDLE()

        class _SECURITY_ATTRIBUTES(ctypes.Structure):
            _fields_ = [
                ("nLength", W.DWORD),
                ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", W.BOOL),
            ]

        sa_inherit = _SECURITY_ATTRIBUTES()
        sa_inherit.nLength = ctypes.sizeof(_SECURITY_ATTRIBUTES)
        sa_inherit.bInheritHandle = True
        sa_inherit.lpSecurityDescriptor = None

        if not K.CreatePipe(ctypes.byref(inR), ctypes.byref(inW), ctypes.byref(sa_inherit), 0):
            raise OSError("CreatePipe (in) 失败")
        if not K.CreatePipe(ctypes.byref(outR), ctypes.byref(outW), ctypes.byref(sa_inherit), 0):
            _CloseHandle(inR)
            _CloseHandle(inW)
            raise OSError("CreatePipe (out) 失败")

        # 对齐 winconpty.cpp: DuplicateHandle 使 hInput/hOutput 可继承
        # （CreatePipe 已通过 sa_inherit 设为可继承，无需额外 DuplicateHandle）

        # ── 2. CreateServerHandle (Inheritable=TRUE) ──
        # 对齐 winconpty.cpp:141-147
        server_h = W.HANDLE()
        st = _create_server_handle(server_h, inheritable=True)
        if st != 0:
            _logger.info("CreateServerHandle 失败 0x%08x, 尝试加载驱动后重试", st)
            _ensure_driver_is_loaded()
            st = _create_server_handle(server_h, inheritable=True)
        if st != 0:
            self._cleanup_handles(inR, inW, outR, outW)
            raise OSError(f"CreateServerHandle 失败: 0x{st:08x}")
        _logger.info("CreateServerHandle OK: 0x%x", server_h.value or 0)

        # ── 3. CreateClientHandle("\\Reference", Inheritable=FALSE) ──
        # 对齐 winconpty.cpp:149-155
        ref_h = W.HANDLE()
        st2 = _create_client_handle(ref_h, server_h, "\\Reference", inheritable=False)
        if st2 != 0:
            _CloseHandle(server_h)
            self._cleanup_handles(inR, inW, outR, outW)
            raise OSError(f"CreateClientHandle Reference 失败: 0x{st2:08x}")
        _logger.info("CreateClientHandle Reference OK: 0x%x", ref_h.value or 0)

        # ── 4. Signal Pipe ──
        # 对齐 winconpty.cpp:157-167
        # sa.bInheritHandle = FALSE, 然后 SetHandleInformation(conhostSide, INHERIT, INHERIT)
        sig_r, sig_w = W.HANDLE(), W.HANDLE()
        sa_signal = _SECURITY_ATTRIBUTES()
        sa_signal.nLength = ctypes.sizeof(_SECURITY_ATTRIBUTES)
        sa_signal.bInheritHandle = False
        sa_signal.lpSecurityDescriptor = None

        if not K.CreatePipe(ctypes.byref(sig_r), ctypes.byref(sig_w), ctypes.byref(sa_signal), 0):
            _CloseHandle(server_h)
            _CloseHandle(ref_h)
            self._cleanup_handles(inR, inW, outR, outW)
            raise OSError("CreatePipe (signal) 失败")

        K.SetHandleInformation.restype = W.BOOL
        K.SetHandleInformation.argtypes = [W.HANDLE, W.DWORD, W.DWORD]
        K.SetHandleInformation(sig_r, _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT)

        # ── 5. 构造 conhost.exe 命令行 ──
        # 对齐 winconpty.cpp:189-204
        conhost = "\\\\?\\" + os.path.join(
            os.environ.get("SystemRoot", "C:\\Windows"),
            "System32", "conhost.exe",
        )
        cmd = (
            f'"{conhost}" --headless --width {cols} --height {rows}'
            f" --signal 0x{sig_r.value:x} --server 0x{server_h.value:x}"
        )
        _logger.info("conhost cmd: %s", cmd)

        # ── 6. HANDLE_LIST + CreateProcessAsUserW ──
        # 对齐 winconpty.cpp:206-271
        hlist_size = ctypes.c_size_t(0)
        _InitAttrList(None, 1, 0, ctypes.byref(hlist_size))
        hlist_buf = ctypes.create_string_buffer(hlist_size.value)
        if not _InitAttrList(hlist_buf, 1, 0, ctypes.byref(hlist_size)):
            _CloseHandle(server_h)
            _CloseHandle(ref_h)
            self._cleanup_handles(inR, inW, outR, outW, sig_r, sig_w)
            raise OSError("InitAttrList 失败 (conhost)")

        # HANDLE_LIST 对齐 winconpty.cpp:214-219
        inh = (W.HANDLE * 4)(server_h, inR, outW, sig_r)
        if not _UpdateAttr(
            hlist_buf,
            0,
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.byref(inh),
            ctypes.sizeof(inh),
            None,
            None,
        ):
            _DeleteAttrList(hlist_buf)
            _CloseHandle(server_h)
            _CloseHandle(ref_h)
            self._cleanup_handles(inR, inW, outR, outW, sig_r, sig_w)
            raise OSError("UpdateAttr HANDLE_LIST 失败")

        sie = _SIE()
        sie.StartupInfo.cb = ctypes.sizeof(_SIE)
        sie.StartupInfo.dwFlags = 0x00000100
        sie.StartupInfo.hStdInput = inR
        sie.StartupInfo.hStdOutput = outW
        sie.StartupInfo.hStdError = outW
        sie.lpAttributeList = ctypes.cast(hlist_buf, ctypes.c_void_p)

        pi = _PI()
        # 对齐 winconpty.cpp:259-270: CreateProcessAsUserW + EXTENDED_STARTUPINFO_PRESENT
        ok = _CreateProcessAsUserW(
            None,
            conhost,
            cmd,
            None,
            None,
            True,
            _EXTENDED_STARTUPINFO_PRESENT,
            None,
            None,
            ctypes.byref(sie.StartupInfo),
            ctypes.byref(pi),
        )
        _DeleteAttrList(hlist_buf)
        if not ok:
            err = ctypes.get_last_error()
            _CloseHandle(server_h)
            _CloseHandle(ref_h)
            self._cleanup_handles(inR, inW, outR, outW, sig_r, sig_w)
            raise OSError(err, "conhost.exe 启动失败")

        _CloseHandle(pi.hThread)
        self._conhost_proc = pi.hProcess
        self._ref_h = ref_h
        self._signal = sig_w
        _logger.info("conhost.exe 启动成功 pid=%d", pi.dwProcessId)

        # 对齐 winconpty.cpp:273-275: 保存 PseudoConsole 成员
        # pPty->hSignal = signalPipeOurSide.release()
        # pPty->hPtyReference = referenceHandle.release()
        # pPty->hConPtyProcess = pi.hProcess

        # 关闭父进程中已继承给 conhost 的句柄副本
        # （winconpty.cpp 中 wil::unique_handle 自动管理，这里手动关闭）
        for h in (server_h, inR, outW, sig_r):
            _CloseHandle(h)

        # 保存我们持有的管道端
        self._inW = inW
        self._outR = outR

        # ── 7. 创建伪 HPCON（用于子进程附着）──
        # 对齐 winconpty.cpp 中 ConptyPackPseudoConsole 的做法
        self._pc = _PSEUDO_CONSOLE()
        self._pc.hSignal = sig_w
        self._pc.hPtyReference = ref_h
        self._pc.hConPtyProcess = pi.hProcess
        self._hpc = ctypes.cast(ctypes.pointer(self._pc), _HPCON)

        # ── 8. 启动子进程（通过 PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE 附着）──
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
        """启动与 HPCON 绑定的子进程

        对齐 winconpty.cpp 中子进程启动方式：
        PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE + CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT
        """
        cmdline = subprocess.list2cmdline(command)
        attr_size = ctypes.c_size_t(0)
        _InitAttrList(None, 1, 0, ctypes.byref(attr_size))
        buf = ctypes.create_string_buffer(attr_size.value)
        if not _InitAttrList(buf, 1, 0, ctypes.byref(attr_size)):
            raise OSError("InitAttrList 失败")
        if not _UpdateAttr(
            buf,
            0,
            _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            ctypes.byref(self._hpc),
            ctypes.sizeof(_HPCON),
            None,
            None,
        ):
            _DeleteAttrList(buf)
            raise OSError("UpdateAttr PSEUDOCONSOLE 失败")
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
            None,
            cmdline,
            None,
            None,
            True,
            _CREATE_UNICODE_ENVIRONMENT | _CREATE_NO_WINDOW | _EXTENDED_STARTUPINFO_PRESENT,
            env_block,
            None,
            ctypes.byref(si.StartupInfo),
            ctypes.byref(pi),
        )
        _DeleteAttrList(buf)
        if not ok:
            raise OSError(ctypes.get_last_error(), "CreateProcessW 失败")
        self._child_pid = pi.dwProcessId
        self._ph = pi.hProcess
        _CloseHandle(pi.hThread)
        self._job.assign(pi.hProcess)
        _logger.info("子进程启动成功 pid=%d", self._child_pid)

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
                self._outR,
                self._buf,
                min(n, len(self._buf)),
                None,
                ctypes.byref(self._ov),
            )
            if ok:
                br = W.DWORD(0)
                _GetOverlappedResult(
                    self._outR,
                    ctypes.byref(self._ov),
                    ctypes.byref(br),
                    False,
                )
                self._pending = False
                return self._buf.raw[: br.value] if br.value else b""
            err = ctypes.get_last_error()
            if err == 109:
                self._pending = False
                return b""
            if err != 997:
                self._pending = False
                raise OSError(err, "ReadFile 失败")
        hs = (W.HANDLE * 2)(self._evt, self._ph)
        _WaitMultiple(2, hs, False, 0xFFFFFFFF)
        self._pending = False
        br = W.DWORD(0)
        ok = _GetOverlappedResult(
            self._outR,
            ctypes.byref(self._ov),
            ctypes.byref(br),
            False,
        )
        data = self._buf.raw[: br.value] if ok and br.value else b""
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
        """关闭所有句柄并清理资源

        对齐 winconpty.cpp _ClosePseudoConsoleMembers:
        关闭 hSignal / hPtyReference / hConPtyProcess
        """
        for h in (self._inW, self._outR):
            if h is not None:
                try:
                    _CancelIoEx(h, None)
                except Exception:
                    pass
                _CloseHandle(h)
        self._inW = None
        self._outR = None
        for h in (self._evt, self._ph, self._signal, self._ref_h, self._conhost_proc):
            if h:
                _CloseHandle(h)
        self._evt = None
        self._ph = None
        self._signal = None
        self._ref_h = None
        self._conhost_proc = None
        self._job.close()
        self._gui_monitor.close()

    def get_type(self) -> str:
        """返回 PTY 后端类型标识"""
        return "win-condrv"

    def get_child_pid(self):
        return self._child_pid

    def get_exit_code(self) -> Optional[int]:
        """获取子进程退出码"""
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
