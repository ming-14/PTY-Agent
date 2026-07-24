"""后台线程管理 — 读者线程与监控线程

管理 Session 的后台读者线程（持续读取 PTY 输出）和独立监控线程
（进程事件、GUI 窗口检测），通过 SessionComponents 数据类接收所有
子组件引用，避免循环依赖。
"""

import errno
import re
import time
import logging
import threading
from dataclasses import dataclass
from typing import Optional, Callable

from ..config.daemon import PTY_READ_SIZE
from ..output import OutputBuffer, TriggerMatcher
from ..process import ProcessMonitor, GuiDetector
from ..terminal.screen import TerminalScreen
from ..protocol.ansi import strip_ansi

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
        screen:        终端屏幕快照管理器。
        session_id:    会话 ID（用于日志）。
        on_exit:       读者线程退出回调，签名 (exit_code: Optional[int], error_message: Optional[str]) -> None。
                       用于通知 Session 更新 running/exit_code/error_message 并关闭 PTY。
    """
    pty_provider: Callable
    out_buf: OutputBuffer
    trig_mat: TriggerMatcher
    proc_mon: ProcessMonitor
    gui_detector: GuiDetector
    screen: TerminalScreen
    session_id: str
    on_exit: Callable
    session_ref: Callable = None


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
                data = pty.read(PTY_READ_SIZE)
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

            # ── 终端查询自动响应（ConPTY 模式下 isTTY=false，需模拟终端回复）──
            self._respond_terminal_queries(data, pty, session_id)

            # ── 排空管道 ──
            drained = pty.drain(PTY_READ_SIZE)
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

            # 同步喂给终端屏幕快照管理器
            # v4: 不再有 suppress 窗口，所有数据都 feed 给 pyte screen
            comp.screen.feed(data)

            # 通知订阅者（Web WS 实时推送）
            # v4: 移除 _suppress_publish_until 检查，让 ConPTY repaint 直达前端
            #     前端通过 resize_complete 中的 snapshot 强制 resync，无需抑制 repaint
            try:
                session = comp.session_ref()
                session._publisher.notify_subscribers(data, "pty")
            except Exception:
                pass

            # 更新 pty 引用（stop 后可能变为 None）
            pty = comp.pty_provider()

        # 读者退出前：扫描残留 GUI 窗口和进程事件
        gui_detector.check(pty, session_id)
        proc_mon.drain_notifications()
        proc_mon.check_events(force=True)

        # 获取退出码（pty 可能已被关闭，容错处理）
        exit_code = None
        error_message = None
        try:
            if pty:
                exit_code = _capture_exit_code_retry(pty)
        except Exception:
            pass
        if exit_code is not None and exit_code != 0:
            # 优先从 output 中提取真实异常信息（如 Python traceback）
            # PTY 模式下 stderr 合并到 stdout，不再有独立 stderr
            stdout_data = out_buf.get_slice() if out_buf else b""
            extracted = _extract_crash_error_from_output(stdout_data)
            if extracted:
                error_message = extracted
            else:
                from ..process import _format_exit_code_message
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

            # 自然退出检测：所有子进程已退出且 PTY 仍在运行，主动关闭 PTY 让 reader EOF
            if pty and not self._stop_event.is_set():
                try:
                    pids = pty.get_process_list()
                    if pids is not None and len(pids) == 0:
                        session = comp.session_ref()
                        if session and session.running:
                            _logger.info(
                                "会话 '%s': 所有子进程已退出，触发自然结束",
                                comp.session_id)
                            session._on_all_processes_exited()
                except Exception:
                    pass

            # 检测子进程控制台鼠标模式变化（MiMo 等 Windows TUI 不发 DECSET）
            if not self._stop_event.is_set():
                try:
                    session = comp.session_ref()
                    if session and session.running:
                        session.update_mouse_mode_from_console()
                except Exception:
                    pass

            self._stop_event.wait(2.0)

    def _respond_terminal_queries(self, data: bytes, pty, session_id: str) -> None:
        """检测子进程发送的终端查询序列并自动回复

        ConPTY 模式下子进程 isTTY=false，渲染器（如 @opentui/core）发送的
        终端能力查询不会被 conhost 回复。此方法拦截这些查询并模拟 xterm-256color
        终端的响应，使渲染器能正常初始化。
        """
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            return

        responses = []

        # DA1 查询: CSI c → 回复 VT520-like
        if "\x1b[c" in text or "\x1b[0c" in text:
            responses.append(b"\x1b[?65;0c")

        # DA2 查询: CSI > c → 回复 VT220
        if "\x1b[>c" in text or "\x1b[>0c" in text:
            responses.append(b"\x1b[>1;0c")

        # CPR 查询: CSI 6n → 光标在 1;1
        if "\x1b[6n" in text:
            responses.append(b"\x1b[1;1R")

        # DECRQM 查询: CSI ? Ps $p → 回复支持
        for m in re.finditer(r"\x1b\[\?(\d+)\$p", text):
            ps = m.group(1)
            responses.append(f"\x1b[?{ps};2$y".encode())

        # XTGETTCAP: DCS + q → 回复支持
        if "\x1bP+q" in text or "\x1bP$q" in text:
            responses.append(b"\x1bP1+r0\x1b\\")

        # Kitty keyboard: CSI ? u → 回复支持
        if "\x1b[?u" in text:
            responses.append(b"\x1b[?u")

        # OSC 10/11 颜色查询
        if "\x1b]10;?" in text:
            responses.append(b"\x1b]10;rgb:ffff/ffff/ffff\x07")
        if "\x1b]11;?" in text:
            responses.append(b"\x1b]11;rgb:0000/0000/0000\x07")

        if responses:
            for resp in responses:
                try:
                    pty.write(resp)
                except Exception:
                    pass
            _logger.debug(
                "终端查询自动响应 (会话 '%s'): 发送 %d 个响应",
                session_id, len(responses))


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


# 常见异常名前缀，用于从输出中识别真实错误
_KNOWN_EXCEPTION_PREFIXES = (
    "ZeroDivisionError", "ValueError", "TypeError", "NameError", "KeyError",
    "IndexError", "AttributeError", "ImportError", "ModuleNotFoundError",
    "RuntimeError", "AssertionError", "OSError", "IOError", "FileNotFoundError",
    "PermissionError", "WindowsError", "SyntaxError", "IndentationError",
    "TabError", "OverflowError", "MemoryError", "RecursionError", "SystemError",
    "ReferenceError", "NotImplementedError", "StopIteration", "GeneratorExit",
    "KeyboardInterrupt", "SystemExit",
)


def _extract_crash_error_from_output(stdout_data: bytes) -> Optional[str]:
    """从 PTY 输出中提取真实异常信息，优先于 Windows 系统错误码翻译

    PTY 模式下 stderr 合并到 stdout，因此仅需分析 stdout_data。

    对 Python 程序，traceback 的最后一行通常是真实异常名与消息；
    对一般程序，尝试识别常见异常前缀或 "Error: ..." 模式。

    Args:
        stdout_data: PTY 输出字节（stderr 已合并至此）。

    Returns:
        提取到的错误描述字符串；无法提取时返回 None。
    """
    data = stdout_data
    if not data:
        return None
    text = strip_ansi(data.decode("utf-8", errors="replace"))
    lines = text.splitlines()

    # Python traceback：找到 "Traceback (most recent call last):" 后取最后一行
    for i, line in enumerate(lines):
        if "Traceback (most recent call last):" in line:
            for j in range(len(lines) - 1, i, -1):
                candidate = lines[j].strip()
                if candidate and (":" in candidate or candidate.endswith(":")):
                    return _clean_error_candidate(candidate)

    # 通用异常前缀匹配（从后往前找最近的异常）
    for line in reversed(lines):
        candidate = line.strip()
        if not candidate:
            continue
        if any(candidate.startswith(prefix + ":") or candidate.startswith(prefix + " ")
               for prefix in _KNOWN_EXCEPTION_PREFIXES):
            return _clean_error_candidate(candidate)
        # 常见的 "XxxError: ..." 或 "Error: ..." 行
        if re.search(r"\b(Error|Exception|Failure|Failed)\b\s*[:：]", candidate, re.IGNORECASE):
            return _clean_error_candidate(candidate)
    return None


# 匹配所有 CSI 序列（ESC [ ... 字母）和 OSC 序列，用于清理提取的异常信息
_CSI_ALL_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?(?:\x07|\x1b\\)')


def _clean_error_candidate(text: str) -> str:
    """清理提取的异常信息行：去除所有 ANSI 控制序列（包括 CSI K 清行等）"""
    return _CSI_ALL_RE.sub("", text).strip()
