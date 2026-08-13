"""win_sandbox.helpers - Python 端辅助工具（ctypes 封装，零依赖）。

补充 C++ 库不做的事：
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

# WriteFile(handle, buf, size, &written, &ov) -> BOOL
_kernel32.WriteFile.argtypes = [
    wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p
]
_kernel32.WriteFile.restype = wintypes.BOOL

# 自定义 OVERLAPPED（ctypes.wintypes 无此结构，仅 hEvent 字段必需）
class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]

# WaitForSingleObject(handle, timeout_ms) -> DWORD (WAIT_OBJECT_0 / WAIT_TIMEOUT / WAIT_FAILED)
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD

# CloseHandle(handle) -> BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

# CreateEventW(None, manual_reset, initial, None) -> HANDLE
_kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateEventW.restype = wintypes.HANDLE

# CancelIoEx(handle, &ov) -> BOOL
_kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
_kernel32.CancelIoEx.restype = wintypes.BOOL

# GetExitCodeProcess(handle, &code) -> BOOL
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL

# 常量
_ERROR_BROKEN_PIPE = 109
_ERROR_NO_DATA = 232
_ERROR_IO_PENDING = 997
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF
_WRITE_TIMEOUT_MS = 30000  # write_pipe 单块写入超时（与文档"30s 上限"一致）


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


def write_pipe(handle: int, data: bytes, timeout_ms: int = _WRITE_TIMEOUT_MS) -> int:
    """OVERLAPPED WriteFile 匿名管道，带超时（30s 默认）。

    子进程不读 stdin（管道满）时最多阻塞 timeout_ms，超时 CancelIoEx 取消后
    抛 OSError，不会无限挂起调用线程；进程退出/管道关闭时抛 OSError(109)。

    Args:
        handle: 管道写端句柄（stdin 写端为 OVERLAPPED 打开）
        data: 要写入的字节
        timeout_ms: 单块写入超时（默认 30000）

    Returns:
        写入字节数

    Raises:
        OSError: WriteFile 失败（超时 / 管道关闭 / 句柄无效）
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(f"write_pipe data must be bytes-like, got {type(data).__name__}")
    if not data:
        return 0

    event = _kernel32.CreateEventW(None, True, False, None)
    if not event:
        raise OSError(ctypes.get_last_error(), "CreateEventW failed")
    try:
        written = wintypes.DWORD()
        ov = _OVERLAPPED()
        ov.hEvent = event
        success = _kernel32.WriteFile(
            ctypes.c_void_p(handle), data, len(data), ctypes.byref(written),
            ctypes.byref(ov)
        )
        if success:
            return written.value

        err = ctypes.get_last_error()
        if err != _ERROR_IO_PENDING:
            raise OSError(f"WriteFile failed: err={err}")

        # 写入挂起：等待完成 / 超时取消
        wait = _kernel32.WaitForSingleObject(event, timeout_ms)
        if wait == _WAIT_TIMEOUT:
            _kernel32.CancelIoEx(ctypes.c_void_p(handle), ctypes.byref(ov))
            _kernel32.WaitForSingleObject(event, _INFINITE)  # 等取消完成
            raise OSError(
                f"WriteFile timed out after {timeout_ms}ms (child not reading); "
                "write cancelled"
            )
        if wait != _WAIT_OBJECT_0:
            raise OSError(ctypes.get_last_error(), "WaitForSingleObject failed")
        return written.value
    finally:
        _kernel32.CloseHandle(event)


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

    仅用于关闭无主的句柄（如独立创建的管道/事件句柄）。
    注意：Process 暴露的 process_handle / stdin_handle / stdout_handle /
    stderr_handle 由库内部管理，禁止用本函数关闭（双重关闭可误关
    句柄值被系统复用后的无关对象）；用 proc.close() / close_stdin() 代替。

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
    """墙钟定时器：超时触发 on_timeout 回调（文档签名 (timeout_ms, on_timeout)）。

    通常配合沙箱墙钟配额使用：超时回调内调 proc.terminate(1)；
    也支持 `with` 上下文管理器（进入即 start，退出 cancel）。

    Attributes:
        fired: 定时器是否已触发
    """

    def __init__(self, timeout_ms: int, on_timeout: Callable[[], None]):
        """
        Args:
            timeout_ms: 超时毫秒
            on_timeout: 超时回调（无参）
        """
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be > 0")
        self._timeout_ms = timeout_ms
        self._on_timeout = on_timeout
        self._fired = False
        self._timer = threading.Timer(timeout_ms / 1000.0, self._fire)
        self._timer.daemon = True

    def _fire(self) -> None:
        self._fired = True
        try:
            self._on_timeout()
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

    # 上下文管理器：with WallClockTimer(ms, cb) as t:
    def __enter__(self) -> "WallClockTimer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cancel()


# =============================================================================
# Stats 轮询
# =============================================================================

class StatsPoller:
    """周期调 proc.query_accounting + 回调。threading.Thread 实现。

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

    # 上下文管理器：with StatsPoller(proc, ms, cb) as poller:
    def __enter__(self) -> "StatsPoller":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


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
