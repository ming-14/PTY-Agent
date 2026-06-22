"""后台线程管理 — 读者线程与监控线程

管理 Session 的后台读者线程（持续读取 PTY 输出）和独立监控线程
（进程事件、GUI 窗口检测），通过 SessionComponents 数据类接收所有
子组件引用，避免循环依赖。
"""

import errno
import time
import logging
import threading
from dataclasses import dataclass
from typing import Optional, Callable

from .output import OutputBuffer, TriggerMatcher
from .process import ProcessMonitor, GuiDetector

_logger = logging.getLogger("pty-session")


@dataclass
class SessionComponents:
    """后台线程所需的所有子组件引用容器

    Attributes:
        pty_provider:  返回当前 PTY 实例的可调用对象（lambda: session._pty）。
        out_buf:       线程安全输出缓冲区。
        trig_mat:      触发条件匹配器。
        proc_mon:      进程树监控器。
        gui_detector:  GUI 窗口检测器。
        session_id:    会话 ID（用于日志）。
        on_exit:       读者线程退出回调，签名 (exit_code: Optional[int], error_message: Optional[str]) -> None。
                       用于通知 Session 更新 running/exit_code/error_message 并关闭 PTY。
    """
    pty_provider: Callable
    out_buf: OutputBuffer
    trig_mat: TriggerMatcher
    proc_mon: ProcessMonitor
    gui_detector: GuiDetector
    session_id: str
    on_exit: Callable


class SessionThreads:
    """后台读者线程与监控线程管理器

    负责：
    - 启动/停止读者线程和监控线程
    - 读者线程持续读取 PTY 输出并追加到 OutputBuffer
    - 监控线程独立检测进程事件和 GUI 窗口
    - 读者线程退出时通过 on_exit 回调通知 Session
    """

    def __init__(self, components: SessionComponents):
        self._comp = components
        self._stop_event = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None

    @property
    def stop_event(self) -> threading.Event:
        """停止信号（Session.stop 也会设置此事件）"""
        return self._stop_event

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self) -> None:
        """启动读者线程和监控线程"""
        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name=f"pty-reader-{self._comp.session_id}",
        )
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name=f"pty-monitor-{self._comp.session_id}",
        )
        self._reader_thread.start()
        self._monitor_thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """停止读者和监控线程

        Args:
            timeout: 等待各线程退出的超时秒数。
        """
        self._stop_event.set()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout)
            if self._reader_thread.is_alive():
                _logger.warning(
                    "读者线程超时未退出 (会话 '%s')",
                    self._comp.session_id)
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout)
            if self._monitor_thread.is_alive():
                _logger.warning(
                    "监控线程超时未退出 (会话 '%s')",
                    self._comp.session_id)

    # ── 后台线程实现 ──────────────────────────────────────────

    def _reader_loop(self) -> None:
        """后台读者线程：持续读取 PTY 输出 → 缓冲 → 触发检测"""
        comp = self._comp
        pty = comp.pty_provider()
        session_id = comp.session_id
        out_buf = comp.out_buf
        trig_mat = comp.trig_mat
        proc_mon = comp.proc_mon
        gui_detector = comp.gui_detector

        while not self._stop_event.is_set() and pty:
            try:
                data = pty.read(65536)
            except OSError as e:
                if e.errno == errno.EBADF:
                    break
                _logger.warning(
                    "读取 PTY 异常 (会话 '%s'): %s", session_id, e)
                break
            except Exception as e:
                _logger.warning(
                    "读取 PTY 异常 (会话 '%s'): %s", session_id, e)
                break
            if not data:
                _logger.info(
                    "会话 '%s': reader EOF (pty read returned empty)",
                    session_id)
                break

            # ── 排空管道 ──
            drained = pty.drain(65536)
            if drained:
                data = data + drained
                _logger.debug(
                    "会话 '%s': drain got %d more bytes, total %d",
                    session_id, len(drained), len(data))

            _logger.debug(
                "会话 '%s': reader got %d bytes: %r",
                session_id, len(data), data[:80])

            # 在 OutputBuffer 锁保护下完成：追加 → 计时 → 触发匹配
            with out_buf.lock:
                if not out_buf.append(data):
                    continue
                trig_mat.on_data_appended(time.monotonic())
                if trig_mat.has_pattern:
                    trig_mat.check(out_buf)

            # 更新 pty 引用（stop 后可能变为 None）
            pty = comp.pty_provider()

        # 读者退出前：扫描残留 GUI 窗口和进程事件
        gui_detector.check(pty, session_id)
        proc_mon.check_events()

        # 获取退出码（pty 可能已被关闭，容错处理）
        exit_code = None
        error_message = None
        try:
            if pty:
                exit_code = _capture_exit_code_retry(pty)
        except Exception:
            pass
        if exit_code is not None and exit_code != 0:
            from .process import _format_exit_code_message
            error_message = _format_exit_code_message(exit_code)

        # 通知 Session：更新 running/exit_code/error_message 并关闭 PTY
        try:
            comp.on_exit(exit_code, error_message)
        except Exception as e:
            _logger.warning(
                "on_exit 回调异常 (会话 '%s'): %s", session_id, e)

    def _monitor_loop(self) -> None:
        """独立监控线程：检测进程事件和 GUI 窗口"""
        comp = self._comp
        while not self._stop_event.is_set():
            pty = comp.pty_provider()
            comp.proc_mon.drain_notifications()
            comp.gui_detector.check(pty, comp.session_id)
            comp.proc_mon.check_events()
            self._stop_event.wait(2.0)


# ── 模块级工具函数 ──────────────────────────────────────────────


def _capture_exit_code_retry(pty, retries: int = 10) -> Optional[int]:
    """带重试地获取子进程退出码（模块级工具函数）

    某些 PTY 后端在进程刚退出时可能尚未更新退出码，通过短暂重试提高成功率。

    Args:
        pty:     PTY 后端实例（提供 get_exit_code 方法）。
        retries: 最大重试次数（默认 10 次，每次间隔 50ms）。

    Returns:
        退出码；获取失败时返回 None。
    """
    for attempt in range(retries):
        try:
            code = pty.get_exit_code()
        except Exception:
            code = None
        if code is not None:
            return code
        if attempt < retries - 1:
            time.sleep(0.05)
    return None
