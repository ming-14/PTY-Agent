"""后台线程管理 — 读者线程与监控线程

管理 Session 的后台读者线程（持续读取 PTY 输出）和独立监控线程
（进程事件、GUI 窗口检测），通过 Components 数据类接收所有
子组件引用，避免循环依赖。
"""

import errno
import logging
import re
import select
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from ..config.daemon import PTY_READ_SIZE
from .buffer import OutputBuffer
from .trigger_matcher import TriggerMatcher
from ..process import GuiDetector, ProcessMonitor
from ..process.base import PendingEvent
from ..protocol.ansi import strip_ansi
from ..terminal.screen import TerminalScreen
from ..logging import get_logger

if TYPE_CHECKING:
    from ..plugins.host import PluginHost
    from ..process.base import ProcessTreeTracker

_logger = get_logger("pty-session")

# 程序发起的尺寸变更（CSI 8;rows;colst，xterm 文本区 resize 请求）。
# wezterm 终端模型忽略该序列，需在 reader 侧解析并落到 PTY/屏幕。
_PROGRAM_RESIZE_RE = re.compile(rb"\x1b\[8;(\d+);(\d+)t")


def _detect_program_resize(data: bytes, comp) -> None:
    """检测程序发起的尺寸变更（CSI 8;rows;colst）并应用

    解析后调用 session._apply_program_resize：更新 PTY/屏幕并经
    publisher 广播（web 端立即响应）。
    """
    m = _PROGRAM_RESIZE_RE.search(data)
    if not m:
        return
    rows, cols = int(m.group(1)), int(m.group(2))
    _logger.info(
        "会话 '%s': 检测到程序尺寸变更 %dx%d", comp.session_id, cols, rows
    )
    session = comp.session_ref()
    if session is None:
        return
    try:
        session._apply_program_resize(cols, rows)
    except Exception as e:
        _logger.warning("会话 '%s': 程序尺寸变更应用失败: %s", comp.session_id, e)


@dataclass
class Components:
    """后台线程所需的所有子组件引用容器

    Attributes:
        pty_provider:  返回当前后端实例的可调用对象（lambda: session._pty）。
        out_buf:       线程安全输出缓冲区（pty=主输出；subprocess=stdout）。
        err_buf:       stderr 输出缓冲区（仅子进程模式，否则 None）。
        trig_mat:      触发条件匹配器。
        proc_mon:      进程树监控器。
        tracker:       进程树追踪器（自然结束检测的进程列表来源）。
        gui_detector:  GUI 窗口检测器。
        screen:        终端屏幕快照管理器（pty 模式；子进程模式为 None）。
        session_id:    会话 ID（用于日志）。
        on_exit:       读者线程退出回调，签名 (exit_code, error_message) -> None。
        session_ref:   返回当前 Session 的可调用对象。
        plugin_host:   插件宿主。
        mode:          会话模式（"pty" | "subprocess"）。
    """

    pty_provider: Callable
    out_buf: OutputBuffer
    trig_mat: TriggerMatcher
    proc_mon: ProcessMonitor
    tracker: "ProcessTreeTracker"
    gui_detector: GuiDetector
    screen: Optional[TerminalScreen]
    session_id: str
    on_exit: Callable
    session_ref: Callable = None
    # 插件宿主（字符串注解避免会话线程层反向依赖插件包实体）
    plugin_host: "PluginHost" = None
    # stderr 缓冲（子进程模式）
    err_buf: Optional[OutputBuffer] = None
    # 会话模式
    mode: str = "pty"


class Threads:
    """后台读者线程与监控线程管理器

    负责：
    - 启动/停止读者线程和监控线程
    - 读者线程持续读取 PTY 输出并追加到 OutputBuffer
    - 监控线程独立检测进程事件和 GUI 窗口
    - 读者线程退出时通过 on_exit 回调通知 Session
    """

    def __init__(self, components: Components):
        self._comp = components
        self._stop_event = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        # 鼠标追踪模式缓存：reader feed 后比对，变化时推送 mouse_mode 事件
        # （web 端据此同步前端状态，后端终端模型为权威源）
        self._last_mouse_mode: Optional[bool] = None

    @property
    def stop_event(self) -> threading.Event:
        """停止信号（Session.stop 也会设置此事件）"""
        return self._stop_event

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self) -> None:
        """启动读者线程和监控线程"""
        self._stop_event.clear()
        # 初始化鼠标模式基线：首个 feed 不产生虚假变化事件
        if self._comp.screen is not None:
            try:
                self._last_mouse_mode = self._comp.screen.is_mouse_tracking()
            except Exception:
                self._last_mouse_mode = None
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
        # 会话自然结束时 stop 由读者线程自身调用（_reader_loop → on_exit → 回调链），
        # join 当前线程会抛 RuntimeError 导致后续清理（tracker.close 等）被跳过；
        # 线程即将退出，无需 join 自己。
        current = threading.current_thread()
        self._stop_event.set()
        if (
            self._reader_thread
            and self._reader_thread is not current
            and self._reader_thread.is_alive()
        ):
            self._reader_thread.join(timeout)
            if self._reader_thread.is_alive():
                _logger.warning("读者线程超时未退出 (会话 '%s')", self._comp.session_id)
        if (
            self._monitor_thread
            and self._monitor_thread is not current
            and self._monitor_thread.is_alive()
        ):
            self._monitor_thread.join(timeout)
            if self._monitor_thread.is_alive():
                _logger.warning("监控线程超时未退出 (会话 '%s')", self._comp.session_id)

    # ── 后台线程实现 ──────────────────────────────────────────

    def _reader_loop(self) -> None:
        """后台读者线程：持续读取后端输出 → 缓冲 → 触发检测"""
        comp = self._comp
        pty = comp.pty_provider()
        session_id = comp.session_id
        out_buf = comp.out_buf
        trig_mat = comp.trig_mat
        proc_mon = comp.proc_mon
        gui_detector = comp.gui_detector
        is_sub = comp.mode == "subprocess"

        while not self._stop_event.is_set() and pty:
            if is_sub:
                # 子进程模式：等待数据事件，非阻塞取 stdout/stderr 双流
                sub_pty = pty
                # 等待新数据或 EOF（带超时以响应 stop_event）
                sub_pty.data_event.wait(0.5)
                sub_pty.data_event.clear()
                datas = []
                try:
                    out_data = sub_pty.read(PTY_READ_SIZE)
                    if out_data:
                        datas.append((out_data, "stdout"))
                except Exception:
                    pass
                if comp.err_buf is not None:
                    try:
                        err_data = sub_pty.read_stderr(PTY_READ_SIZE)
                        if err_data:
                            datas.append((err_data, "stderr"))
                    except Exception:
                        pass
                if not datas:
                    # 双流均 EOF 且进程结束 → reader 退出
                    if sub_pty.is_eof():
                        break
                    pty = comp.pty_provider()
                    continue
            else:
                # ── select 等待（pty 模式）──
                pty_fd = pty.fileno() if hasattr(pty, "fileno") else None
                if pty_fd is not None:
                    try:
                        readable, _, _ = select.select([pty_fd], [], [], 0.5)
                        if not readable:
                            # 超时，继续循环检查 stop_event
                            pty = comp.pty_provider()
                            continue
                    except (OSError, ValueError):
                        # select 出错，PTY 可能已关闭
                        break

                try:
                    data = pty.read(PTY_READ_SIZE)
                except OSError as e:
                    if e.errno == errno.EBADF:
                        break
                    _logger.warning("读取 PTY 异常 (会话 '%s'): %s", session_id, e)
                    break
                except Exception as e:
                    _logger.warning("读取 PTY 异常 (会话 '%s'): %s", session_id, e)
                    break
                if not data:
                    # select  readable 后 read 返回空表示真 EOF（slave 已关闭）
                    _logger.info(
                        "会话 '%s': reader EOF (pty read returned empty after select)",
                        session_id,
                    )
                    break

                # ── 排空管道 ──
                drained = pty.drain(PTY_READ_SIZE)
                if drained:
                    data = data + drained
                    _logger.debug(
                        "会话 '%s': drain got %d more bytes, total %d",
                        session_id,
                        len(drained),
                        len(data),
                    )
                datas = [(data, "stdout")]

            # 热路径：chunk 列表推导仅在 DEBUG 启用时求值（默认 INFO 不构建）
            if _logger.isEnabledFor(logging.DEBUG):
                _logger.debug(
                    "会话 '%s': reader got %d bytes chunks: %r",
                    session_id,
                    len(datas),
                    [len(d) for d, _ in datas],
                )

            # ── 双流分别处理（子进程模式）或单流（pty 模式）──
            for chunk, stream in datas:
                self._process_chunk(comp, pty, chunk, stream, is_sub)

            # 更新 pty 引用（stop 后可能变为 None）
            pty = comp.pty_provider()

        # 读者退出前：扫描残留 GUI 窗口和进程事件
        gui_detector.check(comp.tracker, session_id)
        proc_mon.drain_notifications()
        proc_mon.check_events(force=True)

        # 获取退出码（pty 可能已被关闭，容错处理）
        exit_code = None
        error_message = None
        try:
            # 优先 tracker 已收尸的退出码（Unix 唯一 waitpid 收尸点：
            # 先经 tracker 收尸后 pty try_wait 因进程已回收拿不到）
            code = comp.tracker.get_root_exit_code()
            if code is None and pty:
                code = _capture_exit_code_retry(pty)
            exit_code = code
        except Exception:
            pass
        if exit_code is not None and exit_code != 0:
            # 优先从 output 中提取真实异常信息（如 Python traceback）
            stdout_data = out_buf.get_slice() if out_buf else b""
            if comp.err_buf is not None:
                try:
                    stderr_data = comp.err_buf.get_slice()
                    stdout_data = stdout_data + stderr_data
                except Exception:
                    pass
            extracted = _extract_crash_error_from_output(stdout_data)
            if extracted:
                error_message = extracted
            else:
                from ..process import _format_exit_code_message

                error_message = _format_exit_code_message(exit_code)

        # 通知 Session：更新 running/exit_code/error_message 并关闭后端
        try:
            comp.on_exit(exit_code, error_message)
        except Exception as e:
            _logger.warning("on_exit 回调异常 (会话 '%s'): %s", session_id, e)

    def _process_chunk(self, comp, pty, data: bytes, stream: str, is_sub: bool) -> None:
        """处理单个输出块：插件变换 → 缓冲 → 触发 → 屏幕 → 推送

        pty 模式 stream="stdout"（单缓冲）；子进程模式 stream 区分 stdout/stderr。
        """
        session_id = comp.session_id
        out_buf = comp.out_buf
        trig_mat = comp.trig_mat

        # ── 插件输出变换链 ──
        if comp.plugin_host is not None:
            data = comp.plugin_host.on_output(data)
            if not data:
                _logger.debug(
                    "会话 '%s': 输出被插件吞掉, plugins=%s",
                    session_id,
                    comp.plugin_host.names(),
                )
                return

        # 选择目标缓冲（子进程模式 stderr → err_buf；否则 out_buf）
        target = comp.err_buf if (is_sub and stream == "stderr") else out_buf

        # 在 OutputBuffer 锁保护下完成：追加 → 计时 → 触发匹配
        with target.lock:
            if not target.append(data):
                return
            trig_mat.on_data_appended(time.monotonic())
            if trig_mat.has_pattern:
                trig_mat.check(target)

        # ── 终端屏幕（仅 pty 模式）──
        if not is_sub and comp.screen is not None:
            comp.screen.feed(data)

            # ── 鼠标追踪模式变化检测（web 端权威同步）──
            # 终端模型 feed 后查询模式状态，变化时以 mouse_mode 事件通知订阅者。
            # 前端据此实时同步 appMouseMode，不再依赖字节流嗅探恢复模式
            # （修复"退出 TUI 后鼠标模式残留/丢失"）。
            try:
                new_mouse = comp.screen.is_mouse_tracking()
                if new_mouse != self._last_mouse_mode:
                    self._last_mouse_mode = new_mouse
                    session = comp.session_ref()
                    if session is not None:
                        session._on_event(
                            PendingEvent(
                                timestamp=time.time(),
                                type="mouse_mode",
                                detail={"enabled": bool(new_mouse)},
                            )
                        )
                        _logger.debug(
                            "会话 '%s': mouse tracking -> %s",
                            session_id,
                            "ON" if new_mouse else "OFF",
                        )
            except Exception:
                pass

            # ── 终端查询应答回写（DA1/CPR/XTGETTCAP/OSC 等）──
            try:
                resp = comp.screen.drain_terminal_response()
                if resp:
                    pty.write(resp)
                    _logger.debug(
                        "会话 '%s': 终端应答回写 %d 字节: %r",
                        session_id,
                        len(resp),
                        resp[:80],
                    )
            except Exception as e:
                _logger.warning("会话 '%s': 终端应答回写失败: %s", session_id, e)

            # ── 程序发起尺寸变更检测（CSI 8;rows;colst）──
            _detect_program_resize(data, comp)

        # 通知订阅者（Web WS 实时推送）
        try:
            session = comp.session_ref()
            session._publisher.notify_subscribers(data, stream)
        except Exception:
            pass

    def _monitor_loop(self) -> None:
        """独立监控线程：高频进程事件轮询 + 低频兜底/GUI/插件检查

        进程事件（崩溃/退出）需要尽快反馈到 wait_for_trigger，按 0.2s 高频
        排空 tracker 通知（Unix pgid 为轮询实现，Windows IOCP 推送）；
        进程 diff 兜底（check_events）、GUI 检测、插件 poll 与自然退出检测
        均自带节流或开销较大，保持 2s 低频，避免 /proc 等高频扫描。
        """
        comp = self._comp
        slow_deadline = 0.0
        while not self._stop_event.is_set():
            pty = comp.pty_provider()
            comp.proc_mon.drain_notifications()

            now = time.monotonic()
            if now - slow_deadline >= 2.0:
                slow_deadline = now
                # 同一 tick 只取一次进程列表，供 GUI 检测 / 进程 diff / 自然退出
                # 检测共用（Windows 为 QueryInformationJobObject，Unix 为 /proc 全扫）
                try:
                    pids = comp.tracker.get_process_list()
                except Exception:
                    pids = None
                comp.gui_detector.check(comp.tracker, comp.session_id, pids=pids)
                comp.proc_mon.check_events(pids=pids)

                # ── 插件定时触发 ──
                # 按各插件 poll_interval 节流调用 on_poll（最小有效粒度约 2s）
                if comp.plugin_host is not None:
                    comp.plugin_host.poll_tick()

                # 自然退出检测：所有工作进程已退出且 PTY 仍在运行，主动关闭
                # PTY 让 reader EOF。仅在工作进程内判定——宿主进程（Windows
                # OpenConsole 常驻 Job 直至 pty.close）被 get_work_process_list
                # 排除，若计入则工作进程全退后 Job 仍非空，自然结束永不触发。
                if pty and not self._stop_event.is_set() and pids is not None:
                    try:
                        session = comp.session_ref()
                        if session:
                            session.poll_natural_exit()
                    except Exception:
                        pass

            self._stop_event.wait(0.2)


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
    "ZeroDivisionError",
    "ValueError",
    "TypeError",
    "NameError",
    "KeyError",
    "IndexError",
    "AttributeError",
    "ImportError",
    "ModuleNotFoundError",
    "RuntimeError",
    "AssertionError",
    "OSError",
    "IOError",
    "FileNotFoundError",
    "PermissionError",
    "WindowsError",
    "SyntaxError",
    "IndentationError",
    "TabError",
    "OverflowError",
    "MemoryError",
    "RecursionError",
    "SystemError",
    "ReferenceError",
    "NotImplementedError",
    "StopIteration",
    "GeneratorExit",
    "KeyboardInterrupt",
    "SystemExit",
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
        if any(
            candidate.startswith(prefix + ":") or candidate.startswith(prefix + " ")
            for prefix in _KNOWN_EXCEPTION_PREFIXES
        ):
            return _clean_error_candidate(candidate)
        # 常见的 "XxxError: ..." 或 "Error: ..." 行
        if re.search(
            r"\b(Error|Exception|Failure|Failed)\b\s*[:：]", candidate, re.IGNORECASE
        ):
            return _clean_error_candidate(candidate)
    return None


# 匹配所有 CSI 序列（ESC [ ... 字母）和 OSC 序列，用于清理提取的异常信息
_CSI_ALL_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?(?:\x07|\x1b\\)")


def _clean_error_candidate(text: str) -> str:
    """清理提取的异常信息行：去除所有 ANSI 控制序列（包括 CSI K 清行等）"""
    return _CSI_ALL_RE.sub("", text).strip()
