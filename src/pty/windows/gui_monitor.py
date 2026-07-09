"""GuiWindowMonitor — GUI 窗口检测器

轮询 EnumWindows，交叉比对窗口所属进程 PID 是否在 Job Object 内。
一旦检测到被调试程序（含子/孙进程）创建了可见 GUI 窗口，返回窗口信息。

特性:
- 基于 hwnd 去重，同一窗口只上报一次
- 通过 SendMessage(WM_CLOSE) 关闭指定窗口
- 线程安全（使用锁保护内部状态）
- 可随时清空去重状态以强制全量扫描
"""

import ctypes
import logging
from threading import Lock
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field, asdict
from ctypes import wintypes as W

from .convars import (
    _EnumWindows,
    _GetWindowThreadProcessId,
    _GetWindowTextW,
    _GetClassNameW,
    _IsWindowVisible,
    _SendMessageW,
    WM_CLOSE,
    WNDENUMPROC,
)
from .job import ProcessJob

_logger = logging.getLogger("pty-gui-monitor")

_WINDOW_TITLE_MAX = 256
_WINDOW_CLASS_MAX = 256


@dataclass
class GuiWindowInfo:
    """GUI 窗口信息

    Attributes:
        hwnd:       窗口句柄（整数值）。
        pid:        拥有该窗口的进程 PID。
        title:      窗口标题。
        class_name: 窗口类名。
    """
    hwnd: int
    pid: int
    title: str
    class_name: str

    def to_dict(self) -> Dict:
        """转换为字典（用于 JSON 序列化）"""
        return {
            "hwnd": self.hwnd,
            "pid": self.pid,
            "title": self.title,
            "class_name": self.class_name,
        }


class GuiWindowMonitor:
    """GUI 窗口检测器

    每个 PTY 会话关联一个 GuiWindowMonitor，通过 Job Object 追踪进程树，
    定期轮询 EnumWindows 检测新的 GUI 窗口。

    Attributes:
        windows: 已检测到的所有窗口列表。
    """

    def __init__(self, job: Optional[ProcessJob] = None):
        """初始化 GUI 窗口检测器

        Args:
            job: 关联的 ProcessJob 实例。为 None 时 poll() 无操作。
        """
        self._job = job
        self._lock = Lock()
        # 已上报的 hwnd 集合（去重）
        self._known_hwnds: Set[int] = set()
        # 所有已检测到的窗口信息列表
        self._windows: List[GuiWindowInfo] = []

        # EnumWindows 回调 — 必须保持引用防止 GC
        self._enum_cb: WNDENUMPROC = WNDENUMPROC(self._enum_proc)

        # 临时缓冲区（在回调中使用）
        self._temp_target_pids: Set[int] = set()
        self._temp_new_windows: List[GuiWindowInfo] = []

    def poll(self) -> List[GuiWindowInfo]:
        """轮询检测新增 GUI 窗口

        枚举当前所有可见顶层窗口，将 PID 属于 Job 进程树且
        尚未上报的窗口返回。

        Returns:
            新检测到的窗口列表（仅包含本轮新增的）。
        """
        if not self._job:
            return []

        target_pids = set(self._job.query_process_list())
        if not target_pids:
            return []

        with self._lock:
            self._temp_target_pids = target_pids
            self._temp_new_windows = []

            ok = _EnumWindows(self._enum_cb, 0)
            if not ok:
                err = ctypes.get_last_error()
                if err != 0:
                    _logger.debug("EnumWindows 失败: err=%d", err)

            new_windows = list(self._temp_new_windows)
            self._windows.extend(new_windows)
            return new_windows

    def _enum_proc(self, hwnd: int, lparam: int) -> bool:
        """EnumWindows 回调 — 检查窗口是否属于 Job 内进程

        Returns:
            True 继续枚举，False 停止枚举。
        """
        if not _IsWindowVisible(hwnd):
            return True

        # 去重检查：已在集合中的窗口跳过
        if hwnd in self._known_hwnds:
            return True

        # 获取窗口所属进程 PID
        pid = W.DWORD(0)
        _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        if pid.value not in self._temp_target_pids:
            return True

        # 获取窗口标题
        title_buf = ctypes.create_unicode_buffer(_WINDOW_TITLE_MAX)
        _GetWindowTextW(hwnd, title_buf, _WINDOW_TITLE_MAX)
        title = title_buf.value or ""

        # 获取窗口类名
        class_buf = ctypes.create_unicode_buffer(_WINDOW_CLASS_MAX)
        _GetClassNameW(hwnd, class_buf, _WINDOW_CLASS_MAX)
        class_name = class_buf.value or ""

        info = GuiWindowInfo(
            hwnd=hwnd,
            pid=pid.value,
            title=title,
            class_name=class_name,
        )
        self._known_hwnds.add(hwnd)
        self._temp_new_windows.append(info)
        _logger.info("检测到 GUI 窗口: hwnd=0x%X pid=%d title=%r class=%s",
                      hwnd, pid.value, title, class_name)
        return True

    @property
    def windows(self) -> List[GuiWindowInfo]:
        """获取所有已检测到的窗口"""
        with self._lock:
            return list(self._windows)

    def close_window(self, hwnd: int) -> bool:
        """通过 SendMessage(WM_CLOSE) 关闭指定窗口

        Args:
            hwnd: 要关闭的窗口句柄。

        Returns:
            True 表示消息已发送（实际窗口可能未立即关闭）。
        """
        try:
            _SendMessageW(hwnd, WM_CLOSE, 0, 0)
            _logger.info("已发送 WM_CLOSE 到窗口 hwnd=0x%X", hwnd)
            return True
        except Exception as e:
            _logger.warning("关闭窗口 hwnd=0x%X 失败: %s", hwnd, e)
            return False

    def close_process_windows(self, pid: int) -> int:
        """关闭指定进程的所有已检测窗口

        Args:
            pid: 目标进程 PID。

        Returns:
            成功发送 WM_CLOSE 的窗口数量。
        """
        count = 0
        with self._lock:
            for w in list(self._windows):
                if w.pid == pid:
                    if self.close_window(w.hwnd):
                        count += 1
        return count

    def clear(self):
        """清空去重状态和窗口记录

        调用后，下一轮 poll() 将重新上报所有现有窗口。
        """
        with self._lock:
            self._known_hwnds.clear()
            self._windows.clear()

    def close(self):
        """清理资源"""
        self._known_hwnds.clear()
        self._windows.clear()
        self._job = None
        self._enum_cb = None
