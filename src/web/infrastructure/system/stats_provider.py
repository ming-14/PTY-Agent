"""系统资源统计提供者实现。"""

import ctypes
import logging
import time
from ctypes import wintypes
from ...application.ports import SystemStatsProvider, ThreadExecutor
from ...domain.entities import SystemStats

_logger = logging.getLogger("pty-web")


def _get_windows_stats():
    """无 psutil 时，用 ctypes 读取全局内存和最近一次的 CPU 使用率估算。"""
    mem = None
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalMemoryStatusEx.argtypes = [wintypes.LPVOID]
        kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL

        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", wintypes.ULONGLONG),
                ("ullAvailPhys", wintypes.ULONGLONG),
                ("ullTotalPageFile", wintypes.ULONGLONG),
                ("ullAvailPageFile", wintypes.ULONGLONG),
                ("ullTotalVirtual", wintypes.ULONGLONG),
                ("ullAvailVirtual", wintypes.ULONGLONG),
                ("ullAvailExtendedVirtual", wintypes.ULONGLONG),
            ]

        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            mem = stat.dwMemoryLoad
    except Exception:
        pass

    cpu = None
    try:
        _FILETIME = wintypes.FILETIME
        idle1 = _FILETIME()
        kernel1 = _FILETIME()
        user1 = _FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle1), ctypes.byref(kernel1), ctypes.byref(user1)
        )

        def ft2int(ft):
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        idle1v = ft2int(idle1)
        user1v = ft2int(user1)
        kernel1v = ft2int(kernel1)
        time.sleep(0.1)
        idle2 = _FILETIME()
        kernel2 = _FILETIME()
        user2 = _FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle2), ctypes.byref(kernel2), ctypes.byref(user2)
        )
        idle2v = ft2int(idle2)
        user2v = ft2int(user2)
        kernel2v = ft2int(kernel2)
        idle_delta = idle2v - idle1v
        total_delta = (kernel2v - kernel1v) + (user2v - user1v)
        if total_delta > 0:
            cpu = round((1.0 - idle_delta / total_delta) * 100, 1)
    except Exception:
        pass
    return cpu, mem


class SystemStatsProviderImpl(SystemStatsProvider):
    """系统资源统计提供者实现。"""

    def __init__(self, executor: ThreadExecutor):
        self._executor = executor

    async def get_stats(self) -> SystemStats:
        cpu = mem = None
        try:
            import psutil

            cpu = psutil.cpu_percent(interval=0)
            mem_info = psutil.virtual_memory()
            mem = mem_info.percent
        except Exception:
            pass
        if cpu is None or mem is None:
            try:
                cpu, mem = await self._executor.run(_get_windows_stats)
            except Exception:
                pass
        return SystemStats(cpu=cpu, memory=mem)
