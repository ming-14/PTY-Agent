"""核心 Session — PTY 会话管理（精简协调器版本）

管理一个交互式子进程的生命周期，通过组合模式将职责委派给：
- OutputBuffer        线程安全输出缓冲区
- TriggerMatcher      触发条件匹配与空闲超时检测
- ProcessMonitor      进程树 diff、IOCP 排空、崩溃检测
- EventHistoryManager 事件队列与历史记录管理
- EncodingDetector    编码探测与解码状态管理
- GuiDetector         GUI 窗口轮询检测
- SessionThreads      后台读者线程与监控线程管理
- InputInterceptor    SGR 鼠标拦截、键盘 VT 拦截与鼠标动作执行
- SessionPublisher    订阅者与结束回调管理

Session 自身仅保留：PTY 生命周期、I/O 接口、触发条件协调、退出码捕获。
外部访问子组件请通过公开 @property：session.output_buffer / trigger_matcher
/ event_history / process_monitor。
"""

import os
import re
import sys
import uuid
import errno
import time
import logging
import threading
from typing import Optional, List, Tuple, Callable

from ..pty.pty_factory import create_pty
from ..pty.base import PseudoTerminal
from ..config.common import IS_WINDOWS, DEFAULT_COLS, DEFAULT_ROWS
from ..config.daemon import MAX_OUTPUT_BUFFER, MAX_TRIGGER_SCAN, STOP_TIMEOUT
from ..process import (
    _get_process_name,
    _get_process_path,
    _format_exit_code_message,
    _signal_name,
    _format_pty_error,
    ProcessMonitor,
    GuiDetector,
)
from ..output import (
    OutputBuffer,
    TriggerMatcher,
    safe_regex_search,
    EventHistoryManager,
    PendingEvent,
    _events_to_dicts,
)
from ..encoding import EncodingDetector
from ..terminal.screen import TerminalScreen
from ..input import InputInterceptor
from .publisher import SessionPublisher
from .session_threads import (
    SessionThreads, SessionComponents, _capture_exit_code_retry,
    _extract_crash_error_from_output,
)

if IS_WINDOWS:
    from ..pty.windows.win32_error_msg import (
        STILL_ACTIVE, translate_windows_error,
        format_process_exit_code,
    )

_logger = logging.getLogger("pty-session")

# Windows 下发送 Ctrl+C 需要 AttachConsole，而一个线程同时只能附加到一个控制台，
# 多会话并发发送信号时必须串行化，否则会互相抢占控制台归属
_console_lock = threading.Lock() if IS_WINDOWS else None

# 守护进程忽略 CTRL_C_EVENT 的控制台处理器。
# GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0) 会发给控制台上所有进程，
# 守护进程自己也会收到，必须忽略以免被中断。
# 注意：HANDLER_ROUTINE 回调必须保持全局引用，防止 GC 后崩溃。
if IS_WINDOWS:
    import ctypes as _ctypes
    from ctypes import wintypes as _W

    _HANDLER_ROUTINE = _ctypes.WINFUNCTYPE(_W.BOOL, _W.DWORD)

    def _daemon_ctrl_handler(ctrl_type):
        # CTRL_C_EVENT = 0, CTRL_BREAK_EVENT = 1
        # 返回 True 表示已处理，阻止后续处理器（含 Python KeyboardInterrupt）被调用
        return ctrl_type in (0, 1)

    _daemon_ctrl_handler_ref = _HANDLER_ROUTINE(_daemon_ctrl_handler)
    try:
        _ctypes.WinDLL("kernel32", use_last_error=True).SetConsoleCtrlHandler(
            _daemon_ctrl_handler_ref, True)
    except Exception as _e:
        _logger.warning("SetConsoleCtrlHandler 安装失败: %s", _e)


class Session:
    """PTY 会话（协调器）

    管理一个交互式子进程，提供写入输入、读取输出、触发条件检测等功能。
    通过组合模式将具体职责委派给独立的子组件。

    Attributes:
        id:            会话唯一标识符。
        running:       会话是否正在运行。
        command:       启动时执行的命令。
        exit_code:     子进程退出码（None 表示仍在运行）。
        error_message: 子进程退出时的错误描述（None 表示无错误）。
        encoding:      当前自动探测到的编码。
    """

    def __init__(
        self,
        session_id: str,
        command,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
        encoding: Optional[str] = None,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        snapshot_mode: bool = False,
    ):
        self.id = session_id
        self.uid = str(uuid.uuid4())
        self.command = command
        self.running = False
        self.snapshot_mode = snapshot_mode
        self._cwd = cwd
        self._env = env
        self.start_time: float = 0.0
        self.exit_code = None
        self.error_message = None

        self._child_encoding = encoding
        self._enc = EncodingDetector()

        self._cols = cols if cols is not None else DEFAULT_COLS
        self._rows = rows if rows is not None else DEFAULT_ROWS

        self._out_buf = OutputBuffer(max_size=MAX_OUTPUT_BUFFER)
        self._trig_mat = TriggerMatcher(decode_func=self._decode_only)
        self._evt_hist = EventHistoryManager()
        self._proc_mon = ProcessMonitor(
            pty_provider=lambda: self._pty,
            event_sink=self._evt_hist.add_event,
        )
        self._gui = GuiDetector(event_sink=self._evt_hist.add_event)
        self._screen = TerminalScreen(cols=self._cols, rows=self._rows)
        self._threads = SessionThreads(SessionComponents(
            pty_provider=lambda: self._pty,
            out_buf=self._out_buf,
            trig_mat=self._trig_mat,
            proc_mon=self._proc_mon,
            gui_detector=self._gui,
            screen=self._screen,
            session_id=session_id,
            on_exit=self._on_reader_exit,
            session_ref=lambda: self,
        ))

        self._pty: Optional[PseudoTerminal] = None

        self._publisher = SessionPublisher()

        self._input_interceptor = InputInterceptor(
            pty_provider=lambda: self._pty,
            event_sink=self._evt_hist.add_event,
            cols=self._cols,
            rows=self._rows,
        )

        self.client_config: dict = {}
        self._last_snapshot_lines: Optional[List[str]] = None
        # v4: 不再使用 _suppress_publish_until 抑制 ConPTY repaint。
        # 旧逻辑认为 ConPTY 的 partial repaint 含"错误光标定位序列"会覆盖 xterm.js reflow，
        # 实测发现：xterm.js 自身 reflow 无法得知 PTY 真实光标位置，导致 resize 后光标错位
        # （表现为"光标在 dir 输出中间"，见 reference/1.txt）。
        # 新方案：让 ConPTY 输出直达前端，并通过 snapshot 强制 resync（term.reset + write）。

    # ════════════════════════════════════════════════════════════
    # 生命周期
    # ════════════════════════════════════════════════════════════

    def start(self):
        """启动会话：创建 PTY 后端 + 启动后台读者线程和监控线程"""
        if self.running:
            return
        try:
            self._pty = create_pty(
                self.command, self._cols, self._rows, cwd=self._cwd,
                env=self._env, encoding=self._child_encoding)
        except Exception as e:
            self.running = False
            self.error_message = _format_pty_error(e)
            raise RuntimeError(f"创建伪终端失败: {e}") from e

        self._gui.clear()
        self._evt_hist.clear()
        self._trig_mat.clear()
        self._proc_mon.reset()
        if self._pty:
            try:
                pids = self._pty.get_process_list()
                self._proc_mon.reset(
                    initial_pids=set(pids) if pids else set())
            except Exception:
                self._proc_mon.reset()

        self.running = True
        self.start_time = time.time()
        self._threads.start()

        if IS_WINDOWS:
            time.sleep(0.1)

    def stop(self, timeout: float = STOP_TIMEOUT):
        """停止会话：强杀进程树 + 关闭 PTY + 等待读者线程退出"""
        import time as _time
        _t0 = _time.monotonic()
        self.running = False
        self._threads.stop_event.set()
        self._trig_mat.event.set()
        self._proc_mon.crash_event.set()

        if self._pty and self.exit_code is None:
            self._update_exit_info()
            _logger.debug("stop: sid=%r _update_exit_info took %.3fs exit=%s",
                          self.id, _time.monotonic() - _t0, self.exit_code)

        if self._pty:
            _t1 = _time.monotonic()
            try:
                self._pty.kill_tree()
            except Exception as e:
                _logger.warning("强杀进程树时异常: %s", e)
            _logger.debug("stop: sid=%r kill_tree took %.3fs",
                          self.id, _time.monotonic() - _t1)
            _t2 = _time.monotonic()
            try:
                self._pty.close()
            except Exception as e:
                _logger.warning("关闭伪终端时异常: %s", e)
            self._pty = None
            _logger.debug("stop: sid=%r pty.close took %.3fs",
                          self.id, _time.monotonic() - _t2)
        _t3 = _time.monotonic()
        self._threads.stop(timeout)
        _logger.info("stop: sid=%r threads.stop took %.3fs total %.3fs",
                     self.id, _time.monotonic() - _t3, _time.monotonic() - _t0)

    # ════════════════════════════════════════════════════════════
    # I/O
    # ════════════════════════════════════════════════════════════

    def write_input(self, data):
        """写入输入到 PTY

        通过 InputInterceptor 拦截 SGR 鼠标序列和键盘 VT 序列后写入。

        Args:
            data: 要写入的数据（str 或 bytes）。

        Raises:
            RuntimeError: 会话未运行或写入失败。
            TypeError:    data 类型不正确。
        """
        if not self._pty or not self.running:
            raise RuntimeError(f"会话 '{self.id}' 未运行")
        if not isinstance(data, (str, bytes)):
            raise TypeError(
                f"输入数据必须是 str 或 bytes, 收到 {type(data).__name__}",
            )
        _logger.debug("write_input: sid=%r len=%d data=%r", self.id, len(data),
                       data[:200] if isinstance(data, str) else data[:200])

        data = self._input_interceptor.intercept(
            data, self._child_encoding, self.encoding, self.id)

        try:
            if data:
                self._pty.write(data)
        except Exception as e:
            _logger.error("写入输入失败 (会话 '%s'): %s", self.id, e)
            raise RuntimeError(f"写入输入失败: {e}") from e

    def send_signal(self, sig: str):
        """向子进程发送信号（如 SIGINT）

        通过 os.kill / GenerateConsoleCtrlEvent 等方式直接发送信号到子进程。
        """
        if not self._pty or not self.running:
            return
        pid = self._pty.get_child_pid()
        if pid is None:
            return
        import signal as _signal
        if sig == "SIGINT":
            try:
                if IS_WINDOWS:
                    self._send_sigint_windows(pid)
                else:
                    os.kill(pid, _signal.SIGINT)
                    _logger.info("send_signal: sid=%r SIGINT pid=%d (os.kill)", self.id, pid)
            except Exception as e:
                _logger.warning("send_signal failed: sid=%r sig=%s pid=%d err=%s", self.id, sig, pid, e)
        else:
            _logger.warning("send_signal: unsupported sig=%s", sig)

    def _send_sigint_windows(self, pid: int):
        """Windows 下向子进程发送 SIGINT（Ctrl+C）

        子进程以 CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP 创建，
        拥有独立控制台和进程组。通过 AttachConsole 附加到子进程控制台后，
        用 GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0) 向该控制台所有进程发送 Ctrl+C。

        注意：CTRL_C_EVENT 不能针对进程组发送（dwProcessGroupId != 0 时无效），
        必须用 dwProcessGroupId=0 广播。守护进程已安装全局处理器忽略 CTRL_C_EVENT，
        所以只有子进程及其后代会收到信号。

        AttachConsole 会改变当前线程的控制台归属，必须加锁串行化，
        避免多会话同时发送信号时互相干扰。失败时回退到写 \\x03 到 stdin。
        """
        import ctypes
        from ctypes import wintypes as W

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.FreeConsole.argtypes = []
        kernel32.FreeConsole.restype = W.BOOL
        kernel32.AttachConsole.argtypes = [W.DWORD]
        kernel32.AttachConsole.restype = W.BOOL
        kernel32.GenerateConsoleCtrlEvent.argtypes = [W.DWORD, W.DWORD]
        kernel32.GenerateConsoleCtrlEvent.restype = W.BOOL

        CTRL_C_EVENT = 0

        def _fallback_write_ctrl_c():
            """AttachConsole 失败时的兜底：写 \\x03 到 stdin（非真实信号）"""
            try:
                self._pty.write(b'\x03')
                _logger.info("send_signal: sid=%r \\x03 stdin pid=%d (fallback)", self.id, pid)
            except Exception as e:
                _logger.warning("send_signal all methods failed: sid=%r pid=%d err=%s",
                                self.id, pid, e)

        with _console_lock:
            # 先脱离当前控制台（守护进程可能没有控制台，失败也无妨）
            kernel32.FreeConsole()
            try:
                # 附加到子进程的独立控制台
                if not kernel32.AttachConsole(pid):
                    err = ctypes.get_last_error()
                    _logger.debug(
                        "send_signal: AttachConsole failed pid=%d err=%d, fallback to \\x03",
                        pid, err,
                    )
                    _fallback_write_ctrl_c()
                    return
                try:
                    # CTRL_C_EVENT 不能针对进程组发送（pid != 0 时无效），
                    # 必须用 dwProcessGroupId=0 广播给控制台上所有进程；
                    # 守护进程已安装全局处理器忽略它，只有子进程会收到
                    if kernel32.GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0):
                        _logger.info(
                            "send_signal: sid=%r SIGINT pid=%d (AttachConsole+CtrlEvent(0))",
                            self.id, pid,
                        )
                    else:
                        err = ctypes.get_last_error()
                        _logger.warning(
                            "send_signal: GenerateConsoleCtrlEvent failed err=%d, fallback to \\x03",
                            err,
                        )
                        _fallback_write_ctrl_c()
                finally:
                    # 无论成功与否，都脱离子进程控制台，避免影响后续 AttachConsole
                    kernel32.FreeConsole()
            except Exception as e:
                _logger.warning(
                    "send_signal: sid=%r AttachConsole path failed pid=%d err=%s, fallback to \\x03",
                    self.id, pid, e,
                )
                _fallback_write_ctrl_c()

    def perform_mouse_action(self, action: dict) -> dict:
        """执行鼠标动作（委托给 InputInterceptor）"""
        return self._input_interceptor.perform_mouse_action(
            action, self._screen, self.pty_type, self.id, self.running,
            write_fn=self.write_input,
        )

    def update_mouse_mode_from_console(self):
        """通过子进程控制台输入模式检测是否需要鼠标事件（委托给 InputInterceptor）"""
        self._input_interceptor.update_mouse_mode_from_console(self.id, self.running)

    def detect_encoding(self, sample: Optional[bytes] = None) -> Optional[str]:
        """基于已有输出锁定编码，供 WebSocket 等外部订阅者使用"""
        data = sample
        if data is None:
            data = self._out_buf.get_slice(max(0, self._out_buf.length - 4096))
        if data:
            self._enc.detect_decode(data)
        return self.encoding

    def get_output(
        self,
        from_offset: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> str:
        """获取会话输出"""
        data = self._out_buf.get_slice(
            start=from_offset if from_offset is not None else 0)
        return self._enc.detect_decode(data, encoding)

    def get_output_with_offset(
        self,
        from_offset: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> tuple:
        """原子获取会话输出及当前偏移（消除 TOCTOU 竞态）

        Returns:
            (解码后文本, 当前缓冲区字节偏移) 元组。
        """
        data, cur_offset = self._out_buf.get_slice_with_length(
            start=from_offset if from_offset is not None else 0)
        return self._enc.detect_decode(data, encoding), cur_offset

    def get_snapshot(self, keep_ansi: bool = False) -> str:
        """获取终端屏幕快照"""
        return self._screen.snapshot(keep_ansi=keep_ansi)

    def get_cursor_seq(self) -> str:
        """获取光标定位 VT 序列（CSI row;col H + ?25h/l）

        v6 fix: 供 web 层订阅时附加到 replay 末尾，
        确保前端 replayPending 写入 replay 后光标定位到 PTY 真实位置。
        """
        return self._screen.get_cursor_seq()

    def capture_scrollback(self) -> str:
        """捕获 scrollback 历史区为 ANSI 字符串（带 SGR 颜色）

        Phase 3: 供 web 层 subscribe 响应返回给前端，
        前端写入 xterm.js 推入 scrollback 区，实现 F5 刷新/重开浏览器后 scrollback 不丢。

        Returns:
            每行 ANSI 内容 + \\r\\n 的字符串；无 scrollback 时返回 ""。
        """
        return self._screen.capture_scrollback()

    def clear_scrollback(self) -> None:
        """清除 Grid scrollback 历史区

        resize 后 ConPTY repaint 可能触发 index() 将可见区顶部行推入
        scrollback，导致 scrollback 与 snapshot 内容重叠。
        """
        self._screen.clear_scrollback()

    def get_snapshot_diff(self, keep_ansi: bool = False) -> str:
        """获取终端屏幕快照中与上次相比变化的行"""
        current_text = self._screen.snapshot(keep_ansi=keep_ansi)
        current_lines = current_text.split("\n") if current_text else []
        if self._last_snapshot_lines is None:
            self._last_snapshot_lines = current_lines
            return current_text
        diff_lines = []
        max_len = max(len(current_lines), len(self._last_snapshot_lines))
        for i in range(max_len):
            cur = current_lines[i] if i < len(current_lines) else ""
            prev = self._last_snapshot_lines[i] if i < len(self._last_snapshot_lines) else ""
            if cur != prev:
                diff_lines.append(f"{i}:{cur}")
        self._last_snapshot_lines = current_lines
        return "\n".join(diff_lines)

    def get_snapshot_diagnostics(self) -> dict:
        return self._screen.diagnostics()

    def export_screen_buffer(self) -> dict:
        return self._screen.export_buffer()

    def set_snapshot_trigger(self, pattern: Optional[str] = None,
                             idle_timeout: Optional[float] = None,
                             idle_after_first_output: bool = False):
        self._trig_mat.set_snapshot_trigger(
            pattern=pattern,
            idle_timeout=idle_timeout,
            idle_after_first_output=idle_after_first_output,
        )

    def check_snapshot_trigger(self, snapshot_text: str) -> bool:
        return self._trig_mat.check_snapshot(snapshot_text)

    def check_snapshot_idle_timeout(self) -> bool:
        return self._trig_mat.check_idle_timeout()

    def notify_snapshot_changed(self):
        self._trig_mat.notify_snapshot_changed(time.monotonic())

    @property
    def output_offset(self) -> int:
        return self._out_buf.length

    @property
    def pty_type(self) -> str:
        return self._pty.get_type() if self._pty else "none"

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    def resize(self, cols: int, rows: int) -> str:
        """调整终端尺寸（PTY + pyte screen + InputInterceptor）

        v5 方案（对齐 ConPTY 语义）：
        - 先 resize pyte screen（GridScreen.resize 按 ConPTY 语义重排并写回
          pyte.buffer：内容锚顶、光标绑定文本行，与 ConPTY 坐标系完全一致）
        - 再 resize PTY（ConPTY 内部 reflow；宽度变化时会发 repaint）
        - 短暂等待 ConPTY repaint（如果有的话），让 pyte 同步到最新状态
        - 返回 pyte.buffer 的 snapshot（含 VT 颜色序列 + 真实光标位置）
        - 前端收到 snapshot 后 \\x1b[3J + scrollback + \\x1b[2J + snapshot 重建

        关键不变量：snapshot 的可见区内容和光标 == ConPTY 的可见区内容和光标。
        违背此不变量会导致 resize 后 ConPTY 的绝对光标定位（\\x1b[row;colH）
        落在前端显示内容的中间 —— "光标在 dir 输出中间" bug（历史根因：
        旧 Grid.reflow 锚底 reflow 把 scrollback 行提升进可见区，见
        tests/e2e/test_resize_cursor_sync.py 的实证注释）。

        Returns:
            屏幕快照（含 VT 颜色序列与光标位置），供前端重建 buffer 使用。
        """
        cols, rows = int(cols), int(rows)
        _logger.debug("resize: START %dx%d -> %dx%d", self._cols, self._rows, cols, rows)

        # resize 前 cursor 位置（诊断用）
        try:
            old_cursor = self._screen._screen.cursor
            _logger.debug("resize: before pyte.resize cursor=(x=%s y=%s)",
                          getattr(old_cursor, 'x', '?'), getattr(old_cursor, 'y', '?'))
        except Exception:
            pass

        # 1. 先 resize pyte screen（reflow 内容 + 保留光标）
        #    必须在 pty.resize() 之前完成，这样 reader 线程后续读到的 repaint
        #    字节会以新尺寸被 pyte 正确处理，避免"内容错位"竞态
        screen_ok = True
        try:
            self._screen.resize(cols, rows)
        except Exception as e:
            _logger.warning("resize screen failed: %s", e)
            screen_ok = False

        if screen_ok:
            self._cols, self._rows = cols, rows
            self._input_interceptor.resize(cols, rows)

        # 2. resize PTY（ConPTY 内部 reflow + 发送 repaint）
        #    v4: 不再设置 _suppress_publish_until，让 ConPTY repaint 直达前端
        pty_ok = True
        try:
            if self._pty and hasattr(self._pty, "resize"):
                self._pty.resize(cols, rows)
        except Exception as e:
            _logger.warning("resize pty failed: %s", e)
            pty_ok = False

        if not (pty_ok and screen_ok):
            _logger.warning(
                "resize partial (pty_ok=%s, screen_ok=%s), size=%dx%d",
                pty_ok, screen_ok, self._cols, self._rows,
            )

        # 3. 短暂等待 ConPTY repaint（如果有的话）
        #    pyte screen 已有 reflow 后的旧内容，即使 ConPTY 不发 repaint，
        #    快照仍然包含正确的内容和光标位置
        if pty_ok:
            prior_feed = self._screen.feed_count
            _logger.debug("resize: waiting for optional repaint feed, prior_feed_count=%d", prior_feed)
            # 最多等 200ms 让 reader feed repaint
            waited_ms = 0
            for _ in range(20):
                if self._screen.feed_count > prior_feed:
                    break
                time.sleep(0.01)
                waited_ms += 10
            # 若收到 repaint，再等 60ms 让字节稳定
            if self._screen.feed_count > prior_feed:
                stable_ms = 0
                last_count = self._screen.feed_count
                for _ in range(10):
                    time.sleep(0.03)
                    cur_count = self._screen.feed_count
                    if cur_count == last_count:
                        stable_ms += 30
                        if stable_ms >= 60:
                            break
                    else:
                        stable_ms = 0
                        last_count = cur_count
            _logger.debug("resize: waited %dms, feed_count %d→%d (Δ=%d)",
                          waited_ms, prior_feed, self._screen.feed_count,
                          self._screen.feed_count - prior_feed)

        # snapshot 前 cursor 位置（诊断用）
        try:
            new_cursor = self._screen._screen.cursor
            _logger.debug("resize: before snapshot cursor=(x=%s y=%s) hidden=%s",
                          getattr(new_cursor, 'x', '?'),
                          getattr(new_cursor, 'y', '?'),
                          getattr(new_cursor, 'hidden', '?'))
        except Exception:
            pass

        _logger.debug("resize: END, returning snapshot")
        try:
            # 清除 Grid scrollback：resize 后 ConPTY repaint 可能触发 index()
            # 将可见区顶部行推入 scrollback，导致 scrollback 与 snapshot
            # （读 pyte.buffer 可见区）内容重叠，前端 restoreScrollbackAndSnapshot
            # 会将同一内容写两遍（scrollback 区 + 可见区各一份）。
            # resize snapshot 已包含完整可见区状态，scrollback 在 resize 场景下
            # 是 repaint 竞态产生的冗余，清除后由后续正常输出滚动重新产生。
            try:
                self._screen.clear_scrollback()
                _logger.debug("resize: cleared Grid scrollback before snapshot")
            except Exception as e:
                _logger.debug("resize: clear_scrollback failed (non-fatal): %s", e)

            # 关键：snapshot 必须来自 pyte.buffer（ConPTY 真实可见区状态）。
            # GridScreen.resize 已按 ConPTY 语义重排并写回 pyte.buffer，
            # 此处读出的内容和光标与 ConPTY 坐标系完全一致，
            # 前端重建后 ConPTY 的绝对光标定位不会错位。
            snapshot = self._screen.snapshot(keep_ansi=True, include_cursor=True)
            # 诊断日志：快照前 100 字符 + 末尾 60 字符
            preview_head = snapshot[:100].replace('\r', '\\r').replace('\n', '\\n').replace('\x1b', '\\e')
            preview_tail = snapshot[-60:].replace('\r', '\\r').replace('\n', '\\n').replace('\x1b', '\\e')
            _logger.debug("resize: snapshot len=%d head=%r tail=%r", len(snapshot), preview_head, preview_tail)
            # 诊断：scrollback 摘要
            try:
                sb = self._screen.capture_scrollback()
                sb_text = sb.replace('\r', '\\r').replace('\n', '\\n').replace('\x1b', '\\e')
                _logger.debug("resize: scrollback len=%d head=%r", len(sb), sb_text[:120])
            except Exception:
                pass
            return snapshot
        except Exception as e:
            _logger.warning("resize: 返回 snapshot 失败: %s", e)
            return ""

    # ════════════════════════════════════════════════════════════
    # 触发条件
    # ════════════════════════════════════════════════════════════

    def wait_for_initial_output(self, timeout: float = 1.0) -> bool:
        return self._out_buf.first_output_event.wait(timeout)

    def set_trigger(
        self,
        pattern: str,
        newline: bool = False,
        fresh: bool = False,
        start_offset: Optional[int] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
    ):
        self._trig_mat.set(
            pattern=pattern, newline=newline, fresh=fresh,
            start_offset=start_offset,
            idle_timeout=idle_timeout,
            idle_after_first_output=idle_after_first_output,
            buffer_length=self._out_buf.length,
        )
        if fresh:
            self._trig_mat.fresh_cycle = self._out_buf.read_cycle
            return

        self._trig_mat.newline_count = (
            self._out_buf.count_byte(ord("\n")))
        with self._out_buf.lock:
            self._trig_mat.check(self._out_buf)

    def wait_for_trigger(
        self,
        timeout: Optional[float] = None,
        gui_short_circuit: bool = True,
    ):
        if self._trig_mat.matched:
            return True, "matched"
        if self._proc_mon.crash_event.is_set():
            self._proc_mon.clear_crash()
            return False, "crashed"
        if not self.running:
            return False, "ended"
        if gui_short_circuit and self._gui.gui_windows and self._gui.detected_event.is_set():
            self._gui.detected_event.clear()
            return False, "gui_detected"

        deadline = time.time() + (timeout if timeout is not None else 999999.0)
        _last_gui_check = 0.0
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                _logger.info("wait_for_trigger: TIMEOUT id=%r pattern=%r "
                             "timeout=%s", self.id,
                             self._trig_mat.pattern, timeout)
                return False, "timeout"

            if self._trig_mat.check_idle_timeout():
                _logger.info("wait_for_trigger: IDLE_TIMEOUT id=%r "
                             "idle_timeout=%s",
                             self.id, self._trig_mat.idle_timeout)
                return False, "idle_timeout"

            if self._proc_mon.crash_event.is_set():
                self._proc_mon.clear_crash()
                return False, "crashed"

            self._trig_mat.event.wait(min(0.1, remaining))
            if self._trig_mat.matched:
                _logger.info("wait_for_trigger: MATCHED id=%r pattern=%r",
                             self.id, self._trig_mat.pattern)
                return True, "matched"
            if not self.running:
                return False, "ended"

            now = time.time()
            if now - _last_gui_check >= 1.0:
                _last_gui_check = now
                self._gui.check(self._pty, self.id)
            if gui_short_circuit and self._gui.detected_event.is_set():
                self._gui.detected_event.clear()
                return False, "gui_detected"

    def clear_trigger(self):
        _logger.info("clear_trigger: id=%r pattern=%r matched=%s",
                     self.id, self._trig_mat.pattern,
                     self._trig_mat.matched)
        self._trig_mat.clear()
        self._proc_mon.clear_crash()

    # ── 编码委托 ─────────────────────────────────────────────

    def _decode_only(self, data: bytes) -> str:
        return self._enc.decode_only(data)

    # ── 读者退出回调 ─────────────────────────────────────────

    def _on_all_processes_exited(self):
        if not self.running:
            return
        _logger.info("会话 '%s': 所有子进程已退出，调用 stop", self.id)
        threading.Thread(target=self.stop, daemon=True,
                         name=f"pty-stop-{self.id}").start()

    def _on_reader_exit(self, exit_code, error_message):
        if exit_code is not None:
            self.exit_code = exit_code
            if error_message is not None:
                self.error_message = error_message
        _logger.info(
            "会话 '%s': reader exiting, running=%s, exit_code=%s, error_msg=%s",
            self.id, self.running, self.exit_code, self.error_message)
        self.running = False
        self._out_buf.first_output_event.set()
        self._trig_mat.event.set()
        self._publisher.notify_end(self)

    # ── 退出码获取 ────────────────────────────────────────────

    def _update_exit_info(self):
        if not self._pty:
            return
        code = _capture_exit_code_retry(self._pty)
        if code is not None:
            self.exit_code = code
            if code != 0:
                stdout_data = self._out_buf.get_slice() if self._out_buf else b""
                extracted = _extract_crash_error_from_output(stdout_data)
                self.error_message = extracted or _format_exit_code_message(code)
        else:
            self.exit_code = None

    def close_window(self, hwnd: int) -> bool:
        if not self._pty:
            return False
        return self._pty.close_gui_window(hwnd)

    # ════════════════════════════════════════════════════════════
    # 事件管理（委托给 EventHistoryManager）
    # ════════════════════════════════════════════════════════════

    def consume_events(self) -> List[dict]:
        return self._evt_hist.consume_all()

    def peek_events(self) -> List[dict]:
        return self._evt_hist.peek_pending()

    def get_all_events(self, last: Optional[int] = None,
                       since: Optional[float] = None,
                       until: Optional[float] = None) -> List[dict]:
        return self._evt_hist.get_all(last=last, since=since, until=until)

    @staticmethod
    def _events_to_dicts(events: List[PendingEvent]) -> List[dict]:
        return _events_to_dicts(events)

    def check_event_existence(self, ev: dict) -> bool:
        return self._evt_hist.check_existence(
            ev, pty_provider=lambda: self._pty)

    @property
    def pending_event_count(self) -> int:
        return self._evt_hist.pending_count

    # ════════════════════════════════════════════════════════════
    # 子组件公开访问
    # ════════════════════════════════════════════════════════════

    @property
    def output_buffer(self) -> "OutputBuffer":
        return self._out_buf

    @property
    def trigger_matcher(self) -> "TriggerMatcher":
        return self._trig_mat

    @property
    def event_history(self) -> "EventHistoryManager":
        return self._evt_hist

    @property
    def process_monitor(self) -> "ProcessMonitor":
        return self._proc_mon

    @property
    def publisher(self) -> "SessionPublisher":
        return self._publisher

    @property
    def input_interceptor(self) -> "InputInterceptor":
        return self._input_interceptor

    def get_pty_process_list(self) -> list:
        if self._pty:
            return self._pty.get_process_list()
        return []

    def get_pty_child_pid(self):
        if self._pty:
            return self._pty.get_child_pid()
        return None

    @property
    def cwd(self) -> Optional[str]:
        return self._cwd

    # ════════════════════════════════════════════════════════════
    # 状态代理
    # ════════════════════════════════════════════════════════════

    @property
    def encoding(self) -> Optional[str]:
        return self._enc.encoding

    @encoding.setter
    def encoding(self, value: Optional[str]):
        self._enc.encoding = value

    @property
    def _encoding_locked(self) -> bool:
        return self._enc._encoding_locked

    @_encoding_locked.setter
    def _encoding_locked(self, value: bool):
        self._enc._encoding_locked = value

    @property
    def gui_windows(self) -> List[dict]:
        return list(self._gui.gui_windows)

    @gui_windows.setter
    def gui_windows(self, value: List[dict]):
        with self._gui._lock:
            self._gui.gui_windows = value

    @property
    def processes(self) -> List[int]:
        return list(self._gui.processes)

    @processes.setter
    def processes(self, value: List[int]):
        with self._gui._lock:
            self._gui.processes = value
