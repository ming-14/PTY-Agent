"""GuiWindowMonitor — GUI 窗口检测器

轮询 EnumWindows，交叉比对窗口所属进程 PID 是否在进程树内。
进程树通过 `ProcessTreeTracker.get_process_list()` 获取，因此与
具体追踪实现（Job Object / pgid / 未来 sandbox 委派）解耦，
winsandbox 客户端可复用（见 design/process-manager-refactor.md §4.5）。

特性:
- 基于 hwnd 去重，同一窗口只上报一次
- 通过 SendMessage(WM_CLOSE) 关闭指定窗口
- 线程安全（使用锁保护内部状态）
- 可随时清空去重状态以强制全量扫描
"""

import ctypes
from ctypes import wintypes as W
from dataclasses import dataclass
from threading import Lock
from typing import Dict, List, Optional, Set

from ..base import ProcessTreeTracker
from .api import (
    WM_CLOSE,
    WNDENUMPROC,
    _EnumChildWindows,
    _EnumWindows,
    _GetClassNameW,
    _GetWindowTextW,
    _GetWindowThreadProcessId,
    _IsWindowVisible,
    _SendMessageW,
)
from ...logging import get_logger

_logger = get_logger("process-gui-monitor")

_WINDOW_TITLE_MAX = 256
_WINDOW_CLASS_MAX = 256
# 控件文本提取缓冲（对话框文字可能较长，独立于标题缓冲）
_WINDOW_TEXT_MAX = 2048
# 子窗口递归枚举深度上限（防止极端窗口层级导致耗时过长）
_CHILD_ENUM_MAX_DEPTH = 8


@dataclass
class GuiWindowInfo:
    """GUI 窗口信息

    Attributes:
        hwnd:        窗口句柄（整数值）。
        pid:         拥有该窗口的进程 PID。
        title:       窗口标题。
        class_name:  窗口类名。
        text_content: 窗口及子控件（对话框按钮/静态文本等）的完整文本内容。
    """

    hwnd: int
    pid: int
    title: str
    class_name: str
    text_content: str = ""

    def to_dict(self) -> Dict:
        """转换为字典（用于 JSON 序列化）"""
        d = {
            "hwnd": self.hwnd,
            "pid": self.pid,
            "title": self.title,
            "class_name": self.class_name,
        }
        if self.text_content:
            d["text_content"] = self.text_content
        return d


class GuiWindowMonitor:
    """GUI 窗口检测器

    每个进程树追踪器关联一个 GuiWindowMonitor，通过 tracker
    获取进程树 PID，定期轮询 EnumWindows 检测新的 GUI 窗口。

    Attributes:
        windows: 已检测到的所有窗口列表。
    """

    def __init__(self, tracker: Optional[ProcessTreeTracker] = None):
        """初始化 GUI 窗口检测器

        Args:
            tracker: 关联的进程树追踪器。为 None 时 poll() 无操作。
        """
        self._tracker = tracker
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

    def poll(self, pids: Optional[List[int]] = None) -> List[GuiWindowInfo]:
        """轮询检测新增 GUI 窗口

        枚举当前所有可见顶层窗口，将 PID 属于进程树且
        尚未上报的窗口返回，并提取窗口及子控件的文本内容。

        Args:
            pids: 调用方已获取的进程树 PID 列表（同一 tick 复用，
                  避免重复查询）；None 时自行获取。

        Returns:
            新检测到的窗口列表（仅包含本轮新增的，text_content 已填充）。
        """
        if not self._tracker:
            return []

        if pids is None:
            target_pids = set(self._tracker.get_process_list())
        else:
            target_pids = set(pids)
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

        # 锁外提取窗口文本（EnumChildWindows 递归枚举可能较慢，避免长时间持锁）
        for w in new_windows:
            w.text_content = self._extract_window_text(w.hwnd)
            if w.text_content:
                _logger.info(
                    "GUI 窗口文本: hwnd=0x%X text=%s",
                    w.hwnd,
                    w.text_content[:200].replace("\n", "\\n"),
                )

        return new_windows

    def _enum_proc(self, hwnd: int, lparam: int) -> bool:
        """EnumWindows 回调 — 检查窗口是否属于进程树

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
        _logger.info(
            "检测到 GUI 窗口: hwnd=0x%X pid=%d title=%r class=%s",
            hwnd,
            pid.value,
            title,
            class_name,
        )
        return True

    @property
    def windows(self) -> List[GuiWindowInfo]:
        """获取所有已检测到的窗口"""
        with self._lock:
            return list(self._windows)

    def close_window(self, hwnd: int) -> bool:
        """通过 SendMessage(WM_CLOSE) 关闭指定窗口

        仅允许关闭已跟踪的 GUI 窗口，不匹配则返回 False。

        Args:
            hwnd: 要关闭的窗口句柄。

        Returns:
            True 表示消息已发送（实际窗口可能未立即关闭）。
            False 表示 hwnd 不属于已跟踪窗口或发送失败。
        """
        with self._lock:
            if hwnd not in self._known_hwnds:
                _logger.warning("hwnd=0x%X 不属于已跟踪的 GUI 窗口，拒绝关闭", hwnd)
                return False
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

    # ── 窗口文本提取 ──

    def _extract_window_text(self, hwnd: int) -> str:
        """提取窗口及其全部子控件的文本内容

        递归枚举可见子窗口（对话框上的静态文本/按钮/编辑框等控件），
        收集控件文本（GetWindowText 对标准控件返回控件文字）。

        Args:
            hwnd: 目标窗口句柄。

        Returns:
            合并后的文本（换行分隔）；无文本返回空字符串。
        """
        texts: List[str] = []
        title_buf = ctypes.create_unicode_buffer(_WINDOW_TITLE_MAX)
        _GetWindowTextW(hwnd, title_buf, _WINDOW_TITLE_MAX)
        if title_buf.value and title_buf.value.strip():
            texts.append(title_buf.value)

        self._collect_child_text(hwnd, texts, 0)
        return "\n".join(texts)

    def _collect_child_text(self, hwnd: int, texts: List[str], depth: int):
        """递归收集子窗口文本（深度限制防止极端窗口层级导致耗时过长）"""
        if depth >= _CHILD_ENUM_MAX_DEPTH:
            return

        def _child_enum_proc(child_hwnd: int, lparam: int) -> bool:
            if not _IsWindowVisible(child_hwnd):
                return True
            buf = ctypes.create_unicode_buffer(_WINDOW_TEXT_MAX)
            _GetWindowTextW(child_hwnd, buf, _WINDOW_TEXT_MAX)
            text = buf.value or ""
            if text.strip():
                texts.append(text)
            self._collect_child_text(child_hwnd, texts, depth + 1)
            return True

        cb = WNDENUMPROC(_child_enum_proc)
        _EnumChildWindows(hwnd, cb, 0)

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
        self._tracker = None
        self._enum_cb = None
