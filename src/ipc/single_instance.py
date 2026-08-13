"""单实例锁 — Windows 命名互斥 / Unix flock

提供跨平台的进程级单实例保证（守护进程与客户端控制方共用）：
- Windows：命名互斥体 (CreateMutex)，同一用户会话内互斥。
- Unix：基于文件的排他锁 (flock)，锁文件位于 ~/.pty-agent/daemon.lock。

该类只负责"是否已有实例在运行"的判断，不替代共享内存的端口/令牌传递。
"""

import logging
import os
from typing import Optional

from ..config.common import IS_WINDOWS, DATA_DIR
from ..config.daemon import SINGLE_INSTANCE_MUTEX_NAME

_logger = logging.getLogger("pty-ipc")

_ERROR_ALREADY_EXISTS = 183


def _windows_mutex_name() -> str:
    """返回 Windows 命名互斥体名称（Local\\ 限定同用户会话）"""
    return SINGLE_INSTANCE_MUTEX_NAME


def _unix_lock_path() -> str:
    """返回 Unix 锁文件路径"""
    return os.path.join(DATA_DIR, "daemon.lock")


class SingleInstanceLock:
    """跨平台单实例锁

    使用方式：
        lock = SingleInstanceLock()
        if lock.try_acquire():
            try:
                ...  # 运行守护进程
            finally:
                lock.release()
        else:
            ...  # 已有实例在运行

    也可以只检查是否有实例在运行而不持有锁：
        if SingleInstanceLock().is_locked():
            ...
    """

    def __init__(self):
        self._handle: Optional[int] = None
        self._lock_fd: Optional[int] = None

    def try_acquire(self) -> bool:
        """尝试获取单实例锁

        Returns:
            True 表示成功获取锁；False 表示已有其他实例持有锁。
        """
        if IS_WINDOWS:
            return self._try_acquire_windows()
        return self._try_acquire_unix()

    def _try_acquire_windows(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        CreateMutexW = kernel32.CreateMutexW
        CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        CreateMutexW.restype = wintypes.HANDLE
        GetLastError = kernel32.GetLastError
        CloseHandle = kernel32.CloseHandle

        name = _windows_mutex_name()
        handle = CreateMutexW(None, False, name)
        if not handle:
            err = GetLastError()
            _logger.warning("CreateMutex failed: %d", err)
            return False

        if GetLastError() == _ERROR_ALREADY_EXISTS:
            CloseHandle(handle)
            _logger.debug("单实例互斥体已存在，获取锁失败")
            return False

        self._handle = handle
        _logger.debug("已获取单实例互斥体")
        return True

    def _try_acquire_unix(self) -> bool:
        os.makedirs(DATA_DIR, exist_ok=True)
        path = _unix_lock_path()
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            _logger.warning("无法打开锁文件 %s: %s", path, e)
            return False

        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as e:
            _logger.debug("锁文件已被占用，获取锁失败: %s", e)
            try:
                os.close(fd)
            except OSError:
                pass
            return False

        self._lock_fd = fd
        # 写入 PID 便于调试/排查
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            os.fsync(fd)
        except OSError:
            pass
        _logger.debug("已获取 Unix 单实例文件锁")
        return True

    def release(self) -> None:
        """释放已持有的单实例锁"""
        if IS_WINDOWS:
            self._release_windows()
        else:
            self._release_unix()

    def _release_windows(self) -> None:
        if self._handle is None:
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.ReleaseMutex(wintypes.HANDLE(self._handle))
        kernel32.CloseHandle(wintypes.HANDLE(self._handle))
        self._handle = None
        _logger.debug("已释放单实例互斥体")

    def _release_unix(self) -> None:
        if self._lock_fd is None:
            return
        try:
            import fcntl
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._lock_fd)
        except OSError:
            pass
        self._lock_fd = None
        _logger.debug("已释放 Unix 单实例文件锁")

    def is_locked(self) -> bool:
        """检查是否有其他实例持有锁（不获取锁）

        Returns:
            True 表示已有实例在运行；False 表示没有。
        """
        if IS_WINDOWS:
            return self._is_locked_windows()
        return self._is_locked_unix()

    def _is_locked_windows(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        OpenMutexW = kernel32.OpenMutexW
        OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        OpenMutexW.restype = wintypes.HANDLE
        CloseHandle = kernel32.CloseHandle

        SYNCHRONIZE = 0x00100000
        handle = OpenMutexW(SYNCHRONIZE, False, _windows_mutex_name())
        if handle:
            CloseHandle(handle)
            return True
        return False

    def _is_locked_unix(self) -> bool:
        """Unix 没有纯查询 API，尝试获取后立即释放"""
        path = _unix_lock_path()
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return False

        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except (BlockingIOError, OSError):
            return True
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self) -> "SingleInstanceLock":
        self.try_acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    @staticmethod
    def find_owner_pid() -> Optional[int]:
        """查找持有单实例锁的进程 PID

        Windows: 通过 NtQuerySystemInformation 遍历系统句柄表，
                 用 OpenMutexW 获取目标互斥体内核对象指针，匹配持有者。
        Linux:   解析 /proc/locks 找到持有锁文件的进程。

        Returns:
            持有锁的进程 PID，未找到返回 None。
        """
        if IS_WINDOWS:
            return SingleInstanceLock._find_owner_pid_windows()
        return SingleInstanceLock._find_owner_pid_unix()

    @staticmethod
    def _find_owner_pid_windows() -> Optional[int]:
        import ctypes
        import struct
        import os

        kernel32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll

        SYNCHRONIZE = 0x00100000
        STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
        PROCESS_DUP_HANDLE = 0x0040
        SystemExtendedHandleInformation = 64

        ntdll.NtQuerySystemInformation.argtypes = [
            ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        ntdll.NtQuerySystemInformation.restype = ctypes.c_long

        mutex_name = _windows_mutex_name()
        my_pid = os.getpid()

        target_handle = kernel32.OpenMutexW(SYNCHRONIZE, False, mutex_name)
        if not target_handle:
            return None

        try:
            probe_mutex = kernel32.CreateMutexW(None, False, mutex_name + "__probe__")

            buf_size = 0x400000
            buf = None
            for _ in range(3):
                _buf = ctypes.create_string_buffer(buf_size)
                ret_len = ctypes.c_ulong(0)
                status = ntdll.NtQuerySystemInformation(
                    SystemExtendedHandleInformation,
                    _buf, buf_size, ctypes.byref(ret_len),
                )
                if status == STATUS_INFO_LENGTH_MISMATCH:
                    buf_size = ret_len.value + 0x100000
                    continue
                if status != 0:
                    return None
                buf = _buf
                break

            if buf is None:
                return None

            num_handles = struct.unpack_from("<Q", buf, 0)[0]
            entry_size = 40
            header_size = 16

            mutant_type = None
            for i in range(num_handles):
                offset = header_size + i * entry_size
                if offset + entry_size > len(buf):
                    break
                pid = struct.unpack_from("<Q", buf, offset + 8)[0]
                handle_val = struct.unpack_from("<Q", buf, offset + 16)[0]
                if pid == my_pid and handle_val == probe_mutex:
                    mutant_type = struct.unpack_from("<H", buf, offset + 30)[0]
                    break

            if mutant_type is None:
                return None

            target_object = None
            for i in range(num_handles):
                offset = header_size + i * entry_size
                if offset + entry_size > len(buf):
                    break
                pid = struct.unpack_from("<Q", buf, offset + 8)[0]
                handle_val = struct.unpack_from("<Q", buf, offset + 16)[0]
                if pid == my_pid and handle_val == target_handle:
                    target_object = struct.unpack_from("<Q", buf, offset)[0]
                    break

            if target_object is None:
                return None

            owner_pid = None
            for i in range(num_handles):
                offset = header_size + i * entry_size
                if offset + entry_size > len(buf):
                    break
                obj_type_idx = struct.unpack_from("<H", buf, offset + 30)[0]
                if obj_type_idx != mutant_type:
                    continue
                obj_ptr = struct.unpack_from("<Q", buf, offset)[0]
                if obj_ptr != target_object:
                    continue
                pid = struct.unpack_from("<Q", buf, offset + 8)[0]
                if pid != my_pid:
                    owner_pid = pid
                    break
                if owner_pid is None:
                    owner_pid = pid

            return owner_pid

        finally:
            kernel32.CloseHandle(target_handle)

    @staticmethod
    def _find_owner_pid_unix() -> Optional[int]:
        lock_path = _unix_lock_path()
        try:
            with open("/proc/locks", "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 8 and lock_path in parts[-1]:
                        return int(parts[4])
        except (FileNotFoundError, ValueError, IndexError):
            pass
        return None
