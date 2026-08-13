"""win_sandbox.helpers - Python 端辅助工具（ctypes 封装，零依赖）。

Phase 13：补充 C++ 库不做的事：
  - 句柄读写（read_pipe / write_pipe / wait_process / close_handle）
  - wall_clock 定时器（WallClockTimer）
  - stats 轮询（StatsPoller）
  - 管道 drain（drain_stdout / drain_stderr）

所有函数纯 ctypes，不依赖任何第三方库。
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional

# =============================================================================
# ctypes 绑定（kernel32）
# =============================================================================

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ReadFile(handle, buf, size, &read, None) -> BOOL
_kernel32.ReadFile.argtypes = [
    wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID
]
_kernel32.ReadFile.restype = wintypes.BOOL

# WriteFile(handle, buf, size, &written, None) -> BOOL
_kernel32.WriteFile.argtypes = [
    wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID
]
_kernel32.WriteFile.restype = wintypes.BOOL

# WaitForSingleObject(handle, timeout_ms) -> DWORD (WAIT_OBJECT_0 / WAIT_TIMEOUT / WAIT_FAILED)
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD

# CloseHandle(handle) -> BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

# GetExitCodeProcess(handle, &code) -> BOOL
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL

# 常量
_ERROR_BROKEN_PIPE = 109
_ERROR_NO_DATA = 232
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF


# =============================================================================
# 句柄读写
# =============================================================================

def read_pipe(handle: int, size: int = 65536) -> bytes:
    """ReadFile 匿名管道。返回读取的字节。EOF 时返回 b''。

    Args:
        handle: 管道读端句柄（int / HANDLE 值）
        size: 最大读取字节数

    Raises:
        OSError: ReadFile 失败（非 EOF）
    """
    buf = ctypes.create_string_buffer(size)
    read = wintypes.DWORD()
    success = _kernel32.ReadFile(
        ctypes.c_void_p(handle), buf, size, ctypes.byref(read), None
    )
    if not success:
        err = ctypes.get_last_error()
        if err == _ERROR_BROKEN_PIPE:
            return b""
        raise OSError(f"ReadFile failed: err={err}")
    return buf.raw[:read.value]


def write_pipe(handle: int, data: bytes) -> int:
    """WriteFile 匿名管道。返回写入字节数。

    Args:
        handle: 管道写端句柄
        data: 要写入的字节

    Raises:
        OSError: WriteFile 失败
    """
    written = wintypes.DWORD()
    success = _kernel32.WriteFile(
        ctypes.c_void_p(handle), data, len(data), ctypes.byref(written), None
    )
    if not success:
        err = ctypes.get_last_error()
        raise OSError(f"WriteFile failed: err={err}")
    return written.value


def wait_process(handle: int, timeout_ms: int = -1) -> int:
    """WaitForSingleObject 进程句柄。返回退出码。

    Args:
        handle: 进程句柄
        timeout_ms: 超时毫秒，-1 表示无限等待

    Returns:
        进程退出码

    Raises:
        TimeoutError: 等待超时
        OSError: 等待失败
    """
    wait_ms = _INFINITE if timeout_ms < 0 else timeout_ms
    result = _kernel32.WaitForSingleObject(ctypes.c_void_p(handle), wait_ms)
    if result == _WAIT_TIMEOUT:
        raise TimeoutError(f"WaitForSingleObject timed out after {timeout_ms}ms")
    if result == _WAIT_FAILED:
        err = ctypes.get_last_error()
        raise OSError(f"WaitForSingleObject failed: err={err}")
    # WAIT_OBJECT_0: 信号态，获取退出码
    code = wintypes.DWORD()
    if not _kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
        err = ctypes.get_last_error()
        raise OSError(f"GetExitCodeProcess failed: err={err}")
    return code.value


def close_handle(handle: int) -> None:
    """CloseHandle 封装。

    Args:
        handle: 要关闭的句柄

    Raises:
        OSError: 句柄非法或已关闭（err=6 ERROR_INVALID_HANDLE）
    """
    if not _kernel32.CloseHandle(ctypes.c_void_p(handle)):
        err = ctypes.get_last_error()
        raise OSError(err, f"CloseHandle failed: err={err}")


# =============================================================================
# 后台定时器
# =============================================================================

class WallClockTimer:
    """超时调 proc.terminate。threading.Timer 实现。

    用于 wall_clock_timeout_ms 配额超时后终止进程。

    Attributes:
        fired: 定时器是否已触发
    """

    def __init__(self, proc, timeout_ms: int, exit_code: int = 1):
        """
        Args:
            proc: Process 对象（需有 terminate 方法）
            timeout_ms: 超时毫秒
            exit_code: 超时终止时使用的退出码
        """
        self._proc = proc
        self._exit_code = exit_code
        self._fired = False
        self._timer = threading.Timer(timeout_ms / 1000.0, self._fire)
        self._timer.daemon = True

    def _fire(self) -> None:
        self._fired = True
        try:
            self._proc.terminate(self._exit_code)
        except Exception:
            pass

    def start(self) -> None:
        """启动定时器。"""
        self._timer.start()

    def cancel(self) -> None:
        """取消定时器（未触发时）。"""
        self._timer.cancel()

    @property
    def fired(self) -> bool:
        return self._fired


# =============================================================================
# Stats 轮询
# =============================================================================

class StatsPoller:
    """周期调 proc.query_accounting + 回调。threading.Thread 实现。

    用于替代已删除的 C++ StatsCollectorImpl（周期统计采样）。

    回调签名：callback(stats: dict) -> None
    """

    def __init__(self, proc, interval_ms: int, callback: Callable):
        """
        Args:
            proc: Process 对象（需有 query_accounting 方法）
            interval_ms: 轮询间隔毫秒
            callback: 统计回调
        """
        self._proc = proc
        self._interval = interval_ms / 1000.0
        self._cb = callback
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                stats = self._proc.query_accounting()
                self._cb(stats)
            except Exception:
                pass
            self._stop.wait(self._interval)

    def start(self) -> None:
        """启动轮询线程。"""
        self._thread.start()

    def stop(self) -> None:
        """停止轮询线程。"""
        self._stop.set()
        self._thread.join(timeout=5)


# =============================================================================
# 管道 drain（后台线程读管道）
# =============================================================================

def drain_stdout(proc, callback: Callable[[bytes], None],
                 buffer_size: int = 65536) -> threading.Thread:
    """后台线程循环 read_pipe(proc.stdout_handle) → callback(data)。EOF 退出。

    Args:
        proc: Process 对象（需有 stdout_handle 属性）
        callback: 数据回调
        buffer_size: 单次读取缓冲区大小

    Returns:
        后台线程对象（daemon=True）
    """
    def _loop():
        while True:
            try:
                data = read_pipe(proc.stdout_handle, buffer_size)
            except OSError:
                break
            if not data:
                break
            callback(data)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


def drain_stderr(proc, callback: Callable[[bytes], None],
                 buffer_size: int = 65536) -> threading.Thread:
    """后台线程循环 read_pipe(proc.stderr_handle) → callback(data)。EOF 退出。

    可选内置 AccessDenied 关键字扫描：若 data 含 "拒绝访问" / "Access is denied"，
    且 proc 设置了 on_access_denied 回调，则触发。

    Args:
        proc: Process 对象（需有 stderr_handle 属性）
        callback: 数据回调
        buffer_size: 单次读取缓冲区大小

    Returns:
        后台线程对象（daemon=True）
    """
    import win_sandbox_native

    def _loop():
        while True:
            try:
                data = read_pipe(proc.stderr_handle, buffer_size)
            except OSError:
                break
            if not data:
                break
            callback(data)
            # 内置 AccessDenied 扫描
            if win_sandbox_native.contains_access_denied_keyword(data):
                cb = getattr(proc, "on_access_denied", None)
                if cb is not None:
                    try:
                        cb(data)
                    except Exception:
                        pass

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t
