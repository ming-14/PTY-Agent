"""跨侧共享进程工具 —— 进程存在性探测

pid_exists 被 daemon 控制（src/daemonctl）与 daemon 自身（server.py 启动检查）共用。
"""

import os

from ..config.common import IS_WINDOWS


def pid_exists(pid: int) -> bool:
    """检查指定 PID 的进程是否存在（跨平台）

    Windows: OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) 探测句柄；
    Unix: os.kill(pid, 0) 信号探测。
    """
    if IS_WINDOWS:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
