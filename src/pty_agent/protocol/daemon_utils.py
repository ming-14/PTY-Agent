"""共享内存工具 — 守护进程状态与生命周期辅助（协议层）

提供守护进程的存活检测与共享内存残留清理语义，被客户端（daemon 管控）
与守护进程自身（单实例检测）两端共用。仅依赖 config 与 protocol 层。

单实例检测：纯共享内存（PID + 状态 + 心跳），PID 存在且心跳新鲜即视为存活，
无 TCP ping、无端口、无锁文件。
"""

import logging
import os
import time
from typing import Optional

from ..config import (
    DAEMON_HEARTBEAT_FRESH,
    IS_WINDOWS,
)
from .shm import (
    read_daemon_info,
    cleanup_daemon_info,
)
from .auth import cleanup_auth_shm

_logger = logging.getLogger("pty-protocol")


def pid_exists(pid: int) -> bool:
    """检查指定 PID 的进程是否存在

    Args:
        pid: 进程 ID。

    Returns:
        True 表示进程存在。
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


def heartbeat_fresh(heartbeat: float) -> bool:
    """判断心跳时间戳是否新鲜

    Args:
        heartbeat: 心跳时间戳（time.time()）。

    Returns:
        True 表示心跳新鲜（守护进程存活）。
    """
    return (time.time() - heartbeat) <= DAEMON_HEARTBEAT_FRESH


def cleanup_shm_resources():
    """清理共享内存残留（守护进程信息区 + 认证令牌）"""
    cleanup_daemon_info()
    cleanup_auth_shm()


def find_daemon_pid() -> Optional[int]:
    """查找正在运行的守护进程 PID（纯共享内存）

    从共享内存读取 PID + 心跳，进程存在且心跳新鲜视为存活；
    否则清理残留返回 None。

    Returns:
        守护进程 PID，未找到返回 None。
    """
    info = read_daemon_info()
    if info is None:
        return None

    pid, running, heartbeat = info

    if not running:
        _logger.info("共享内存中的守护进程已标记停止，清理残留")
        cleanup_shm_resources()
        return None

    if not pid_exists(pid):
        _logger.info("共享内存中的进程 %d 已不存在，清理残留", pid)
        cleanup_shm_resources()
        return None

    if not heartbeat_fresh(heartbeat):
        _logger.info("进程 %d 心跳过期（%.1fs 前），判定为僵死守护进程",
                     pid, time.time() - heartbeat)
        cleanup_shm_resources()
        return None

    return pid


def daemon_running() -> bool:
    """检查守护进程是否正在运行

    Returns:
        True 表示守护进程在运行。
    """
    return find_daemon_pid() is not None
