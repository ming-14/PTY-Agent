"""ConDrv 直连伪终端 — 复刻 Windows Terminal winconpty.cpp 的 ConPTY 创建方式

本模块与 conpty.py（CreatePseudoConsole API 路径）效果相同，都是创建 ConPTY 伪终端，
区别在于本模块绕过 CreatePseudoConsole API，直接通过 ConDrv 设备路径手动完成相同流程，
与 Windows Terminal src/winconpty/winconpty.cpp _CreatePseudoConsole 实现一致：

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

优势：可指定任意 conhost 路径（如 OpenConsole），而 CreatePseudoConsole API 只能使用系统 conhost。
"""

import os
import ctypes
import subprocess
import logging
import threading
from contextlib import contextmanager
from ctypes import wintypes as W
from typing import Optional, List

from ..base import PseudoTerminal
from ...config.common import DEFAULT_COLS, DEFAULT_ROWS
from .win32_api import (
    K,
    _CONDRV_OK,
    _ReadFile,
    _WriteFile,
    _CloseHandle,
    _GetExitCodeProcess,
    _PeekNamedPipe,
    _InitAttrList,
    _UpdateAttr,
    _DeleteAttrList,
    _CancelIoEx,
    _CreateProcess,
    _CreateProcessAsUserW,
    _NtOpenFile,
    _NtSetSystemInformation,
    _HPCON,
    _UNICODE_STRING,
    _OBJECT_ATTRIBUTES,
    _IO_STATUS_BLOCK,
    _COORD,
    _SIE,
    _PI,
    _PSEUDO_CONSOLE,
    _AttachConsole,
    _FreeConsole,
    _GetConsoleMode,
    _SetConsoleMode,
    _WriteConsoleInputW,
    _MapVirtualKeyW,
    STD_INPUT_HANDLE,
    ENABLE_MOUSE_INPUT,
    ENABLE_EXTENDED_FLAGS,
    ENABLE_QUICK_EDIT_MODE,
    ENABLE_LINE_INPUT,
    ENABLE_ECHO_INPUT,
    ENABLE_PROCESSED_INPUT,
    ENABLE_VIRTUAL_TERMINAL_INPUT,
    MOUSE_EVENT,
    MOUSE_MOVED,
    DOUBLE_CLICK,
    MOUSE_WHEELED,
    MOUSE_HWHEELED,
    FROM_LEFT_1ST_BUTTON_PRESSED,
    RIGHTMOST_BUTTON_PRESSED,
    FROM_LEFT_2ND_BUTTON_PRESSED,
    WHEEL_DELTA,
    SHIFT_PRESSED,
    LEFT_ALT_PRESSED,
    LEFT_CTRL_PRESSED,
    _INPUT_RECORD,
    _MOUSE_EVENT_RECORD,
    KEY_EVENT,
    _KEY_EVENT_RECORD,
    _CreateFileW,
)
from .win32_error_msg import STILL_ACTIVE
from .job import ProcessJob
from .gui_monitor import GuiWindowMonitor, GuiWindowInfo

_logger = logging.getLogger("pty-condrv")

_CONSOLE_ATTACH_LOCK = threading.Lock()

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
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_PTY_SIGNAL_RESIZE_WINDOW = 8
_STARTF_USESTDHANDLES = 0x00000100

_GENERIC_READ_WRITE = _GENERIC_READ | _GENERIC_WRITE
_FILE_SHARE_ALL = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
_OPEN_EXISTING = 3


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
        _FILE_SHARE_ALL,
        open_options,
    )


def _create_server_handle(handle, inheritable=True):
    """对齐 DeviceHandle::CreateServerHandle"""
    return _nt_create_handle(
        handle,
        "\\Device\\ConDrv\\Server",
        _GENERIC_ALL,
        None,
        inheritable,
        0,
    )


def _create_client_handle(handle, server_handle, name, inheritable=False):
    """对齐 DeviceHandle::CreateClientHandle"""
    return _nt_create_handle(
        handle,
        name,
        _GENERIC_READ_WRITE | _SYNCHRONIZE,
        server_handle,
        inheritable,
        _FILE_SYNCHRONOUS_IO_NONALERT,
    )


def _ensure_driver_is_loaded():
    """对齐 winconpty::_EnsureDriverIsLoaded"""
    info = W.ULONG(1)
    _NtSetSystemInformation(132, ctypes.byref(info), ctypes.sizeof(W.ULONG))


def _find_conhost() -> str:
    """查找 conhost 路径：优先 bin/OpenConsole.exe，不存在则回退系统 conhost

    Returns:
        conhost 可执行文件的绝对路径。
    """
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    oc = os.path.join(base, "bin", "openconsole", "OpenConsole.exe")
    if os.path.isfile(oc):
        _logger.info("使用 OpenConsole: %s", oc)
        return oc
    sys_conhost = os.path.join(
        os.environ.get("SystemRoot", "C:\\Windows"),
        "System32", "conhost.exe",
    )
    _logger.info("OpenConsole 不存在，回退系统 conhost: %s", sys_conhost)
    return sys_conhost


@contextmanager
def _attach_conin(pid, caller: str = ""):
    """上下文管理器：AttachConsole(pid) → CreateFileW("CONIN$") → yield 句柄 → 自动清理"""
    with _CONSOLE_ATTACH_LOCK:
        _FreeConsole()
        if not _AttachConsole(pid):
            err = ctypes.get_last_error()
            _logger.debug("%s: AttachConsole(%d) failed err=%d", caller, pid, err)
            yield None
            return
        try:
            hConIn = _CreateFileW(
                "CONIN$",
                _GENERIC_READ | _GENERIC_WRITE,
                _FILE_SHARE_READ | _FILE_SHARE_WRITE,
                None,
                _OPEN_EXISTING,
                0,
                None,
            )
            if not hConIn or hConIn == W.HANDLE(-1):
                err = ctypes.get_last_error()
                _logger.debug("%s: CreateFile CONIN$ failed err=%d", caller, err)
                yield None
            else:
                try:
                    yield hConIn
                finally:
                    _CloseHandle(hConIn)
        finally:
            _FreeConsole()


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

    def __init__(self, command, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS, env=None, cwd=None,
                 encoding: Optional[str] = None):
        if not _CONDRV_OK:
            raise OSError("ConDrv 驱动不可用（需要管理员权限或系统不支持）")

        self._inW: Optional[int] = None
        self._outR: Optional[int] = None
        self._hpc = None
        self._ph = None
        self._child_pid = None
        self._cols = cols
        self._rows = rows
        self._signal = None
        self._ref_h = None
        self._conhost_proc = None
        self._job = ProcessJob(name=f"pty-con-{id(self)}")
        self._gui_monitor = GuiWindowMonitor(job=self._job)

        if encoding:
            _logger.debug("ConDrvPseudoTerminal: encoding=%s (ConPTY output is always UTF-8)", encoding)

        # ── 1. I/O 管道 ──
        # 对齐验证脚本 test_condrv_manual.py：CreatePipe + SetHandleInformation INHERIT
        inR, inW = W.HANDLE(), W.HANDLE()
        outR, outW = W.HANDLE(), W.HANDLE()
        K.CreatePipe(ctypes.byref(inR), ctypes.byref(inW), None, 0)
        K.CreatePipe(ctypes.byref(outR), ctypes.byref(outW), None, 0)
        K.SetHandleInformation(inR, _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT)
        K.SetHandleInformation(outW, _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT)
        _logger.info("I/O pipes: inR=%s inW=%s outR=%s outW=%s",
                     inR.value, inW.value, outR.value, outW.value)

        # ── 2. CreateServerHandle (Inheritable=TRUE) ──
        server_h = W.HANDLE()
        st = _create_server_handle(server_h, inheritable=True)
        if st != 0:
            _logger.info("CreateServerHandle 失败 0x%08x, 尝试加载驱动后重试", st & 0xFFFFFFFF)
            _ensure_driver_is_loaded()
            st = _create_server_handle(server_h, inheritable=True)
        if st != 0:
            self._cleanup_handles(inR, inW, outR, outW)
            raise OSError(f"CreateServerHandle 失败: 0x{st & 0xFFFFFFFF:08x}")
        _logger.info("CreateServerHandle OK: 0x%x", server_h.value or 0)

        # ── 3. CreateClientHandle("\\Reference", Inheritable=FALSE) ──
        ref_h = W.HANDLE()
        st2 = _create_client_handle(ref_h, server_h, "\\Reference", inheritable=False)
        if st2 != 0:
            _CloseHandle(server_h)
            self._cleanup_handles(inR, inW, outR, outW)
            raise OSError(f"CreateClientHandle Reference 失败: 0x{st2 & 0xFFFFFFFF:08x}")
        _logger.info("CreateClientHandle Reference OK: 0x%x", ref_h.value or 0)

        # ── 4. Signal Pipe ──
        # sa.bInheritHandle = FALSE, 然后 SetHandleInformation(conhostSide, INHERIT, INHERIT)
        sig_r, sig_w = W.HANDLE(), W.HANDLE()
        K.CreatePipe(ctypes.byref(sig_r), ctypes.byref(sig_w), None, 0)
        K.SetHandleInformation(sig_r, _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT)
        _logger.info("Signal pipe: sigR=%s sigW=%s", sig_r.value, sig_w.value)

        # ── 5. 构造 conhost.exe 命令行 ──
        # 优先使用 bin/OpenConsole.exe，不存在则回退系统 conhost
        conhost = _find_conhost()
        cmd = (
            f'"{conhost}" --headless --width {cols} --height {rows}'
            f" --signal 0x{sig_r.value:X} --server 0x{server_h.value:X}"
        )
        _logger.info("conhost cmd: %s", cmd)

        # ── 6. HANDLE_LIST + CreateProcessAsUserW ──
        hlist_size = ctypes.c_size_t(0)
        _InitAttrList(None, 1, 0, ctypes.byref(hlist_size))
        hlist_buf = ctypes.create_string_buffer(hlist_size.value)
        if not _InitAttrList(hlist_buf, 1, 0, ctypes.byref(hlist_size)):
            _CloseHandle(server_h)
            _CloseHandle(ref_h)
            self._cleanup_handles(inR, inW, outR, outW, sig_r, sig_w)
            raise OSError("InitAttrList 失败 (conhost)")

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
        sie.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        sie.StartupInfo.hStdInput = inR
        sie.StartupInfo.hStdOutput = outW
        sie.StartupInfo.hStdError = outW
        sie.lpAttributeList = ctypes.cast(hlist_buf, ctypes.c_void_p)

        pi = _PI()
        cmd_buf = ctypes.create_unicode_buffer(cmd)
        conhost_buf = ctypes.create_unicode_buffer(conhost)
        ok = _CreateProcessAsUserW(
            None,
            conhost_buf,
            cmd_buf,
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

        # 关闭父进程中已继承给 conhost 的句柄副本
        for h in (server_h, inR, outW, sig_r):
            _CloseHandle(h)

        # 保存我们持有的管道端
        self._inW = inW
        self._outR = outR

        # ── 7. 创建伪 HPCON（用于子进程附着）──
        self._pc = _PSEUDO_CONSOLE()
        self._pc.hSignal = sig_w
        self._pc.hPtyReference = ref_h
        self._pc.hConPtyProcess = pi.hProcess
        self._hpc = ctypes.cast(ctypes.pointer(self._pc), _HPCON)

        # ── 8. 启动子进程（通过 PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE 附着）──
        self._start_child(command, env, cwd)

    @staticmethod
    def _cleanup_handles(*handles):
        """安全关闭多个句柄，忽略 None 和关闭异常"""
        for h in handles:
            if h is not None:
                try:
                    _CloseHandle(h)
                except Exception:
                    pass

    def _start_child(self, command, env, cwd=None):
        """启动与 HPCON 绑定的子进程

        对齐 conpty.py 的 CreateProcessW 调用方式：
        - bInheritHandles = False（伪控制台句柄通过属性传递，不需要继承）
        - dwFlags = STARTF_USESTDHANDLES（hStdInput/Output/Error 保持 NULL）
        - ConPTY 内核驱动自动为子进程分配伪控制台句柄
        """
        cmdline = subprocess.list2cmdline(command)
        cmdline_buf = ctypes.create_unicode_buffer(cmdline)
        attr_size = ctypes.c_size_t(0)
        _InitAttrList(None, 1, 0, ctypes.byref(attr_size))
        buf = ctypes.create_string_buffer(attr_size.value)
        if not _InitAttrList(buf, 1, 0, ctypes.byref(attr_size)):
            raise OSError("InitAttrList 失败")
        if not _UpdateAttr(
            buf,
            0,
            _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            self._hpc,
            ctypes.sizeof(_HPCON),
            None,
            None,
        ):
            _DeleteAttrList(buf)
            raise OSError("UpdateAttr PSEUDOCONSOLE 失败")
        si = _SIE()
        si.StartupInfo.cb = ctypes.sizeof(_SIE)
        si.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        si.lpAttributeList = ctypes.cast(buf, ctypes.c_void_p)

        env_block = None
        env_dict = os.environ.copy()
        if isinstance(env, dict):
            env_dict.update(env)
        env_block = ctypes.create_unicode_buffer(
            "\0".join(f"{k}={v}" for k, v in env_dict.items()) + "\0\0",
        )
        pi = _PI()
        ok = _CreateProcess(
            None,
            cmdline_buf,
            None,
            None,
            False,
            _CREATE_UNICODE_ENVIRONMENT | _EXTENDED_STARTUPINFO_PRESENT,
            env_block,
            cwd,
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
        buf = ctypes.create_string_buffer(n)
        br = W.DWORD(0)
        if not _ReadFile(self._outR, buf, n, ctypes.byref(br), None):
            err = ctypes.get_last_error()
            if err == 109:
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
        """写入输入管道"""
        if isinstance(data, str):
            data = data.encode()
        if b'\x1b[<' in data:
            _logger.info("write: %d bytes SGR_MOUSE data=%r", len(data), data[:200])
        else:
            _logger.debug("write: %d bytes", len(data))
        if not self._inW:
            raise OSError("输入管道已关闭")
        wr = W.DWORD(0)
        if not _WriteFile(self._inW, data, len(data), ctypes.byref(wr), None):
            raise OSError(ctypes.get_last_error(), "WriteFile 失败")

    def resize(self, cols: int, rows: int):
        """通过信号管道发送 resize 信号

        对齐 winconpty.cpp _ResizePseudoConsole：
          unsigned short signalPacket[3];
          signalPacket[0] = PTY_SIGNAL_RESIZE_WINDOW;
          signalPacket[1] = size.X;
          signalPacket[2] = size.Y;
          WriteFile(hSignal, signalPacket, sizeof(signalPacket), ...);

        sizeof(signalPacket) = 6 字节（3 × uint16）。
        必须用 <HHH 而非 <Ihh：后者会输出 8 字节，conhost 读取前 6 字节
        会把 cols 解析为 0，触发 cmd.exe 崩溃 (0xC000013A STATUS_CONTROL_C_EXIT)。
        """
        if not self._signal:
            return
        try:
            import struct
            # <HHH = 3 × unsigned short (little-endian) = 6 字节
            msg = struct.pack("<HHH", _PTY_SIGNAL_RESIZE_WINDOW, cols, rows)
            wr = W.DWORD(0)
            _WriteFile(self._signal, msg, len(msg), ctypes.byref(wr), None)
            self._cols = cols
            self._rows = rows
            _logger.debug("resize: %dx%d (signal %d bytes)", cols, rows, len(msg))
        except Exception as e:
            _logger.warning("resize failed: %s", e)

    def _cleanup(self, caller: str = ""):
        """统一清理：CancelIoEx → Job.close → 关闭管道 → 关闭进程句柄 → GUI

        关闭顺序很关键（避免死锁）：
        1. CancelIoEx(_outR) → 取消 reader 线程挂起的 ReadFile
        2. 关闭 Job (KILL_ON_JOB_CLOSE) → 立即终止所有子进程
        3. 关闭 conhost 进程句柄 → conhost 退出
        4. 关闭管道句柄 → reader 后续 read 也立即失败
        5. 关闭信号管道 + 引用句柄 + GUI 监控
        """
        if self._outR:
            try:
                _CancelIoEx(self._outR, None)
            except Exception:
                pass
        if self._job:
            try:
                self._job.close()
            except Exception as e:
                _logger.warning("%s: job.close failed: %s", caller, e)
        if self._outR:
            try:
                _CloseHandle(self._outR)
            except Exception:
                pass
            self._outR = None
        if self._inW:
            try:
                _CloseHandle(self._inW)
            except Exception:
                pass
            self._inW = None
        for h in (self._signal, self._ref_h, self._conhost_proc, self._ph):
            if h:
                try:
                    _CloseHandle(h)
                except Exception:
                    pass
        self._signal = None
        self._ref_h = None
        self._conhost_proc = None
        self._ph = None
        self._gui_monitor.close()

    def kill_tree(self):
        """强杀整个进程树"""
        import time as _time
        _t0 = _time.monotonic()
        _logger.info("kill_tree: pid=%d", self._child_pid)
        self._cleanup("kill_tree")
        _logger.info("kill_tree: done pid=%d total %.3fs", self._child_pid, _time.monotonic() - _t0)

    def close(self):
        """关闭伪终端并清理资源"""
        _logger.info("close: pid=%d", self._child_pid)
        self._cleanup("close")

    def get_type(self) -> str:
        return "win-condrv"

    def is_vt_input_enabled(self) -> bool:
        """检查子进程是否启用了 ENABLE_VIRTUAL_TERMINAL_INPUT"""
        import time as _time
        if hasattr(self, '_vt_input_cache') and hasattr(self, '_vt_input_cache_time'):
            if _time.monotonic() - self._vt_input_cache_time < 3.0:
                return self._vt_input_cache

        pid = self._child_pid
        if not pid:
            return False

        with _attach_conin(pid, "is_vt_input_enabled") as hConIn:
            if not hConIn:
                return False
            mode = W.DWORD()
            if _GetConsoleMode(hConIn, ctypes.byref(mode)):
                result = bool(mode.value & ENABLE_VIRTUAL_TERMINAL_INPUT)
                self._vt_input_cache = result
                self._vt_input_cache_time = _time.monotonic()
                _logger.info("is_vt_input_enabled: pid=%d mode=0x%x vt_input=%s",
                             pid, mode.value, result)
                return result

        return False

    def reset_vt_input_cache(self):
        if hasattr(self, '_vt_input_cache'):
            del self._vt_input_cache

    def get_console_input_mode(self) -> Optional[int]:
        """读取子进程控制台输入模式"""
        pid = self._child_pid
        if not pid:
            return None

        with _attach_conin(pid, "get_console_input_mode") as hConIn:
            if not hConIn:
                return None
            mode = W.DWORD()
            if _GetConsoleMode(hConIn, ctypes.byref(mode)):
                _logger.debug("get_console_input_mode: pid=%d mode=0x%x", pid, mode.value)
                return mode.value

        return None

    def is_tui_mouse_input_enabled(self) -> bool:
        """更宽松的 TUI 鼠标模式检测"""
        pid = self._child_pid
        mode = self.get_console_input_mode()
        if mode is None:
            _logger.info("is_tui_mouse_input_enabled: pid=%d mode=None → False", pid)
            return False
        has_mouse = bool(mode & ENABLE_MOUSE_INPUT)
        has_vt = bool(mode & ENABLE_VIRTUAL_TERMINAL_INPUT)
        line = bool(mode & ENABLE_LINE_INPUT)
        echo = bool(mode & ENABLE_ECHO_INPUT)
        raw = not line and not echo
        result = raw and (has_mouse or has_vt)
        _logger.info("is_tui_mouse_input_enabled: pid=%d mode=0x%x has_mouse=%s has_vt=%s line=%s echo=%s raw=%s → %s",
                     pid, mode, has_mouse, has_vt, line, echo, raw, result)
        return result

    @staticmethod
    def _vt_sequence_to_key_records(data: bytes):
        """将 VT 键盘序列解析为 (wVirtualKeyCode, wVirtualScanCode, UnicodeChar, dwControlKeyState) 列表"""
        VK_UP = 0x26
        VK_DOWN = 0x28
        VK_LEFT = 0x25
        VK_RIGHT = 0x27
        VK_HOME = 0x24
        VK_END = 0x23
        VK_INSERT = 0x2D
        VK_DELETE = 0x2E
        VK_PRIOR = 0x21
        VK_NEXT = 0x22
        VK_F1 = 0x70
        VK_F2 = 0x71
        VK_F3 = 0x72
        VK_F4 = 0x73
        VK_F5 = 0x74
        VK_F6 = 0x75
        VK_F7 = 0x76
        VK_F8 = 0x77
        VK_F9 = 0x78
        VK_F10 = 0x79
        VK_F11 = 0x7A
        VK_F12 = 0x7B
        VK_TAB = 0x09
        ENHANCED_KEY = 0x0100

        _CSI_CURSOR_MAP = {
            ord('A'): (VK_UP, ENHANCED_KEY),
            ord('B'): (VK_DOWN, ENHANCED_KEY),
            ord('C'): (VK_RIGHT, ENHANCED_KEY),
            ord('D'): (VK_LEFT, ENHANCED_KEY),
            ord('H'): (VK_HOME, ENHANCED_KEY),
            ord('F'): (VK_END, ENHANCED_KEY),
            ord('P'): (VK_F1, 0),
            ord('Q'): (VK_F2, 0),
            ord('R'): (VK_F3, 0),
            ord('S'): (VK_F4, 0),
        }

        _CSI_GENERIC_MAP = {
            1: (VK_HOME, ENHANCED_KEY),
            2: (VK_INSERT, ENHANCED_KEY),
            3: (VK_DELETE, ENHANCED_KEY),
            4: (VK_END, ENHANCED_KEY),
            5: (VK_PRIOR, ENHANCED_KEY),
            6: (VK_NEXT, ENHANCED_KEY),
            15: (VK_F5, 0),
            17: (VK_F6, 0),
            18: (VK_F7, 0),
            19: (VK_F8, 0),
            20: (VK_F9, 0),
            21: (VK_F10, 0),
            23: (VK_F11, 0),
            24: (VK_F12, 0),
        }

        _SS3_MAP = {
            ord('A'): (VK_UP, ENHANCED_KEY),
            ord('B'): (VK_DOWN, ENHANCED_KEY),
            ord('C'): (VK_RIGHT, ENHANCED_KEY),
            ord('D'): (VK_LEFT, ENHANCED_KEY),
            ord('F'): (VK_END, ENHANCED_KEY),
            ord('H'): (VK_HOME, ENHANCED_KEY),
            ord('P'): (VK_F1, 0),
            ord('Q'): (VK_F2, 0),
            ord('R'): (VK_F3, 0),
            ord('S'): (VK_F4, 0),
        }

        results = []
        i = 0
        n = len(data)
        while i < n:
            if data[i] != 0x1b:
                i += 1
                continue
            if i + 1 >= n:
                break

            next_byte = data[i + 1]

            if next_byte == ord('['):
                j = i + 2
                params_start = j
                while j < n and (0x30 <= data[j] <= 0x3F):
                    j += 1
                while j < n and (0x20 <= data[j] <= 0x2F):
                    j += 1
                if j >= n:
                    break
                final = data[j]
                param_str = data[params_start:j].decode('ascii', errors='replace')

                if final == ord('Z'):
                    sc = _MapVirtualKeyW(VK_TAB, 0) if _MapVirtualKeyW else 0
                    results.append((i, j + 1, VK_TAB, sc, '\t', SHIFT_PRESSED))
                    i = j + 1
                    continue

                if final in _CSI_CURSOR_MAP:
                    vk, enhanced = _CSI_CURSOR_MAP[final]
                    modifier_state = 0
                    if param_str and param_str != '':
                        parts = param_str.split(';')
                        if len(parts) >= 2:
                            try:
                                mod_param = int(parts[1])
                                if mod_param > 1:
                                    vt_mod = mod_param - 1
                                    if vt_mod & 1:
                                        modifier_state |= SHIFT_PRESSED
                                    if vt_mod & 2:
                                        modifier_state |= LEFT_ALT_PRESSED
                                    if vt_mod & 4:
                                        modifier_state |= LEFT_CTRL_PRESSED
                            except ValueError:
                                pass
                    modifier_state |= enhanced
                    wch = chr(_MapVirtualKeyW(vk, 2)) if _MapVirtualKeyW else '\0'
                    sc = _MapVirtualKeyW(vk, 0) if _MapVirtualKeyW else 0
                    results.append((i, j + 1, vk, sc, wch, modifier_state))
                    i = j + 1
                    continue

                if final == ord('~') and param_str:
                    parts = param_str.split(';')
                    try:
                        key_num = int(parts[0])
                    except ValueError:
                        i = j + 1
                        continue
                    if key_num in _CSI_GENERIC_MAP:
                        vk, enhanced = _CSI_GENERIC_MAP[key_num]
                        modifier_state = 0
                        if len(parts) >= 2:
                            try:
                                mod_param = int(parts[1])
                                if mod_param > 1:
                                    vt_mod = mod_param - 1
                                    if vt_mod & 1:
                                        modifier_state |= SHIFT_PRESSED
                                    if vt_mod & 2:
                                        modifier_state |= LEFT_ALT_PRESSED
                                    if vt_mod & 4:
                                        modifier_state |= LEFT_CTRL_PRESSED
                            except ValueError:
                                pass
                        modifier_state |= enhanced
                        wch = chr(_MapVirtualKeyW(vk, 2)) if _MapVirtualKeyW else '\0'
                        sc = _MapVirtualKeyW(vk, 0) if _MapVirtualKeyW else 0
                        results.append((i, j + 1, vk, sc, wch, modifier_state))
                        i = j + 1
                        continue

                i = j + 1
                continue

            if next_byte == ord('O'):
                if i + 2 >= n:
                    break
                final = data[i + 2]
                if final in _SS3_MAP:
                    vk, enhanced = _SS3_MAP[final]
                    modifier_state = enhanced
                    wch = chr(_MapVirtualKeyW(vk, 2)) if _MapVirtualKeyW else '\0'
                    sc = _MapVirtualKeyW(vk, 0) if _MapVirtualKeyW else 0
                    results.append((i, i + 3, vk, sc, wch, modifier_state))
                    i = i + 3
                    continue
                i = i + 2
                continue

            i += 1

        return results if results else None

    def inject_key_events(self, key_specs: list) -> bool:
        """批量注入键盘事件到子进程控制台输入缓冲区"""
        pid = self._child_pid
        if not pid:
            _logger.warning("inject_key_events: no child pid")
            return False
        if not key_specs:
            return True

        with _attach_conin(pid, "inject_key_events") as hConIn:
            if not hConIn:
                return False

            records = []
            for vk, sc, wch, mod_state in key_specs:
                for key_down in (True, False):
                    rec = _INPUT_RECORD()
                    rec.EventType = KEY_EVENT
                    ke = rec.Event.KeyEvent
                    ke.bKeyDown = key_down
                    ke.wRepeatCount = 1
                    ke.wVirtualKeyCode = vk
                    ke.wVirtualScanCode = sc
                    ke.UnicodeChar = wch
                    ke.dwControlKeyState = mod_state
                    records.append(rec)

            buf = (_INPUT_RECORD * len(records))(*records)
            written = W.DWORD()
            ok = _WriteConsoleInputW(hConIn, buf, len(records), ctypes.byref(written))

            if ok:
                _logger.info("inject_key_events: pid=%d keys=%d records=%d written=%d",
                             pid, len(key_specs), len(records), written.value)
                return True
            else:
                err = ctypes.get_last_error()
                _logger.warning("inject_key_events: WriteConsoleInputW failed err=%d", err)
                return False

    def inject_vt_bytes(self, data: bytes) -> bool:
        """将原始 VT 字节作为 KEY_EVENT_RECORD 注入子进程控制台输入缓冲区"""
        pid = self._child_pid
        if not pid:
            _logger.warning("inject_vt_bytes: no child pid")
            return False

        with _attach_conin(pid, "inject_vt_bytes") as hConIn:
            if not hConIn:
                return False

            records = []
            for byte_val in data:
                ch = chr(byte_val)
                for key_down in (True, False):
                    rec = _INPUT_RECORD()
                    rec.EventType = KEY_EVENT
                    ke = rec.Event.KeyEvent
                    ke.bKeyDown = key_down
                    ke.wRepeatCount = 1
                    ke.wVirtualKeyCode = byte_val if byte_val < 0x80 else 0
                    ke.wVirtualScanCode = 0
                    ke.UnicodeChar = ch
                    ke.dwControlKeyState = 0
                    records.append(rec)

            buf = (_INPUT_RECORD * len(records))(*records)
            written = W.DWORD()
            ok = _WriteConsoleInputW(hConIn, buf, len(records), ctypes.byref(written))
            if ok:
                _logger.info("inject_vt_bytes: pid=%d bytes=%d records=%d written=%d data=%r",
                             pid, len(data), len(records), written.value, data[:200])
                return True
            else:
                err = ctypes.get_last_error()
                _logger.warning("inject_vt_bytes: WriteConsoleInputW failed err=%d", err)
                return False

    @staticmethod
    def _decode_sgr_to_mouse_record(x: int, y: int, button: int, is_release: bool,
                                     double_click: bool = False):
        """SGR button 解码 → (dwButtonState, dwEventFlags, dwControlKeyState)"""
        sgr_modifiers = button & 0x1c
        win_modifiers = 0
        if sgr_modifiers & 0x04:
            win_modifiers |= SHIFT_PRESSED
        if sgr_modifiers & 0x08:
            win_modifiers |= LEFT_ALT_PRESSED
        if sgr_modifiers & 0x10:
            win_modifiers |= LEFT_CTRL_PRESSED

        button_state = 0
        event_flags = 0

        is_wheel = (button & 0x40) != 0
        is_drag = (button & 0x20) != 0
        base_button = button & 0x03

        if is_wheel:
            wheel_code = button & 0x03
            if wheel_code == 0:
                delta = WHEEL_DELTA
            elif wheel_code == 1:
                delta = -WHEEL_DELTA
            elif wheel_code == 2:
                delta = -WHEEL_DELTA
                event_flags = MOUSE_HWHEELED
            else:
                delta = WHEEL_DELTA
                event_flags = MOUSE_HWHEELED
            if event_flags != MOUSE_HWHEELED:
                event_flags = MOUSE_WHEELED
            button_state = (delta & 0xFFFF) << 16
        elif is_drag:
            event_flags = MOUSE_MOVED
            if base_button == 0:
                button_state = FROM_LEFT_1ST_BUTTON_PRESSED
            elif base_button == 1:
                button_state = FROM_LEFT_2ND_BUTTON_PRESSED
            elif base_button == 2:
                button_state = RIGHTMOST_BUTTON_PRESSED
            else:
                button_state = 0
                event_flags = 0
        elif is_release or base_button == 3:
            button_state = 0
        else:
            if base_button == 0:
                button_state = FROM_LEFT_1ST_BUTTON_PRESSED
            elif base_button == 1:
                button_state = FROM_LEFT_2ND_BUTTON_PRESSED
            elif base_button == 2:
                button_state = RIGHTMOST_BUTTON_PRESSED
            if double_click and not is_release:
                event_flags = DOUBLE_CLICK

        return button_state, event_flags, win_modifiers

    def _build_mouse_record(self, x: int, y: int, button: int, is_release: bool,
                            double_click: bool = False) -> _INPUT_RECORD:
        """构造单个 MOUSE_EVENT_RECORD INPUT_RECORD"""
        button_state, event_flags, win_modifiers = self._decode_sgr_to_mouse_record(
            x, y, button, is_release, double_click=double_click)
        rec = _INPUT_RECORD()
        rec.EventType = MOUSE_EVENT
        me = ctypes.cast(ctypes.byref(rec.Event), ctypes.POINTER(_MOUSE_EVENT_RECORD)).contents
        me.dwMousePosition.X = x
        me.dwMousePosition.Y = y
        me.dwButtonState = button_state
        me.dwControlKeyState = win_modifiers
        me.dwEventFlags = event_flags
        return rec

    def inject_mouse_events(self, events: List[tuple]) -> bool:
        """批量注入多个鼠标事件到子进程控制台输入缓冲区"""
        pid = self._child_pid
        if not pid:
            _logger.warning("inject_mouse_events: no child pid")
            return False
        if not events:
            return True

        with _attach_conin(pid, "inject_mouse_events") as hConIn:
            if not hConIn:
                return False

            mode = W.DWORD()
            if _GetConsoleMode(hConIn, ctypes.byref(mode)):
                new_mode = mode.value | ENABLE_MOUSE_INPUT | ENABLE_EXTENDED_FLAGS
                new_mode &= ~ENABLE_QUICK_EDIT_MODE
                if _SetConsoleMode(hConIn, new_mode):
                    verify_mode = W.DWORD()
                    if _GetConsoleMode(hConIn, ctypes.byref(verify_mode)):
                        _logger.info("inject_mouse_events: CONIN$ mode 0x%x -> 0x%x (verify=0x%x, events=%d)",
                                     mode.value, new_mode, verify_mode.value, len(events))
                    else:
                        _logger.info("inject_mouse_events: CONIN$ mode 0x%x -> 0x%x (verify failed, events=%d)",
                                     mode.value, new_mode, len(events))
                else:
                    err = ctypes.get_last_error()
                    _logger.warning("inject_mouse_events: SetConsoleMode failed err=%d", err)
            else:
                err = ctypes.get_last_error()
                _logger.warning("inject_mouse_events: GetConsoleMode failed err=%d", err)

            records = []
            for ev in events:
                x, y, button, is_release = ev[0], ev[1], ev[2], ev[3]
                double_click = ev[4] if len(ev) > 4 else False
                rec = self._build_mouse_record(x, y, button, is_release,
                                               double_click=double_click)
                records.append(rec)

            buf = (_INPUT_RECORD * len(records))(*records)
            written = W.DWORD()
            ok = _WriteConsoleInputW(hConIn, buf, len(records), ctypes.byref(written))

            if ok:
                for ev, rec in zip(events, records):
                    x, y, button, is_release = ev[0], ev[1], ev[2], ev[3]
                    double_click = ev[4] if len(ev) > 4 else False
                    me = ctypes.cast(ctypes.byref(rec.Event), ctypes.POINTER(_MOUSE_EVENT_RECORD)).contents
                    _logger.debug("inject_mouse_events: pid=%d pos=(%d,%d) button=%d release=%s "
                                  "dc=%s state=0x%x flags=0x%x mods=0x%x",
                                  pid, x, y, button, is_release, double_click,
                                  me.dwButtonState, me.dwEventFlags, me.dwControlKeyState)
                import time as _time
                _time.sleep(0.05)
                _logger.info("inject_mouse_events: %d events written, waited 50ms for tcell", len(records))
                return True
            else:
                err = ctypes.get_last_error()
                _logger.warning("inject_mouse_events: WriteConsoleInputW failed err=%d", err)
                return False

    def inject_mouse_event(self, x: int, y: int, button: int, is_release: bool,
                           control_key_state: int = 0) -> bool:
        """直接注入单个鼠标事件（向后兼容接口）"""
        return self.inject_mouse_events([(x, y, button, is_release)])

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
