"""核心 Session — 会话管理（文本管线协调器）

管理一个交互式子进程的生命周期，通过组合模式将职责委派给：
- Backend             运行后端（subprocess 管道 / TTY）
- OutputPipeline      输出管线（subprocess→文本流 / pty→pyte 屏幕）
- OutputBuffer        线程安全文本行输出缓冲 + 游标
- TriggerMatcher      触发条件匹配与空闲超时检测
- ProcessMonitor      IOCP 通知排空、崩溃检测
- EventHistoryManager 事件队列与历史记录管理
- GuiDetector         GUI 窗口轮询检测
- SessionThreads      后台读者线程与监控线程管理

Session 自身仅保留：后端生命周期、I/O 接口、触发条件协调、退出码捕获。
外部访问子组件请通过公开 @property：session.output_buffer / trigger_matcher
/ event_history / process_monitor。
"""

import time
import logging
from typing import Optional, List

from ..backend.factory import create_subprocess, create_tty
from ..backend.base import Backend
from ..config import IS_WINDOWS, DEFAULT_COLS, DEFAULT_ROWS
from .process import (
    _format_exit_code_message,
    _format_backend_error,
    ProcessMonitor,
    GuiDetector,
)
from .output import (
    OutputBuffer,
    TriggerMatcher,
    EventHistoryManager,
    StreamPipeline,
    ScreenPipeline,
)
from .session_threads import SessionThreads, SessionComponents, _capture_exit_code_retry
from .wake import WakeSignal

_logger = logging.getLogger("pty-session")


class Session:
    """会话（协调器）

    管理一个交互式子进程，提供写入输入、读取输出、触发条件检测等功能。
    通过组合模式将具体职责委派给独立的子组件。

    Attributes:
        id:            会话唯一标识符。
        running:       会话是否正在运行。
        command:       启动时执行的命令。
        mode:          后端模式（"subprocess" / "pty"）。
        exit_code:     子进程退出码（None 表示仍在运行）。
        error_message: 子进程退出时的错误描述（None 表示无错误）。
    """

    def __init__(
        self,
        session_id: str,
        command,
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
        shell: Optional[str] = None,
        cwd: Optional[str] = None,
        pty: bool = False,
    ):
        self.id = session_id
        self.command = command
        self.running = False
        self._mode = "pty" if pty else "subprocess"
        self._shell = shell
        self._cwd = cwd
        self.start_time: float = 0.0  # 会话启动时间戳（Unix 时间）
        self.exit_code = None
        self.error_message = None

        # ── 子组件（使用不冲突的内部名，避免 __getattr__ 名称干扰）──
        # 共享唤醒锚：触发命中 / GUI 新窗口 / 崩溃 / 退出 均经它唤醒等待循环，
        # 使这些平级返回条件获得同量级的响应延迟（而非各自等下一个轮询周期）。
        self._wake = WakeSignal()
        self._out_buf = OutputBuffer()
        self._trig_mat = TriggerMatcher(wake=self._wake)
        self._evt_hist = EventHistoryManager()
        self._proc_mon = ProcessMonitor(
            pty_provider=lambda: self._pty,
            event_sink=self._evt_hist.add_event,
            wake=self._wake,
        )
        self._gui = GuiDetector(event_sink=self._evt_hist.add_event,
                                wake=self._wake)
        self._pipeline = None
        self._threads = SessionThreads(SessionComponents(
            pty_provider=lambda: self._pty,
            pipeline_provider=lambda: self._pipeline,
            out_buf=self._out_buf,
            trig_mat=self._trig_mat,
            proc_mon=self._proc_mon,
            gui_detector=self._gui,
            session_id=session_id,
            on_exit=self._on_reader_exit,
        ))

        # 后端
        self._pty: Optional[Backend] = None

        # 终端尺寸
        self._cols = cols
        self._rows = rows

    # ════════════════════════════════════════════════════════════
    # 生命周期
    # ════════════════════════════════════════════════════════════

    def start(self):
        """启动会话：创建后端 + 输出管线 + 启动后台读者线程和监控线程"""
        if self.running:
            return
        try:
            if self._mode == "pty":
                # 真实终端：命令必须为列表（无 shell 语法），失败不回退
                self._pty = create_tty(
                    self.command, self._cols, self._rows, cwd=self._cwd)
                self._pipeline = ScreenPipeline(
                    cols=self._cols, rows=self._rows, out_buf=self._out_buf)
            else:
                # 纯管道子进程：字符串命令可经 shell 执行
                self._pty = create_subprocess(
                    self.command, shell=self._shell, cwd=self._cwd,
                    cols=self._cols, rows=self._rows,
                )
                self._pipeline = StreamPipeline(out_buf=self._out_buf)
        except Exception as e:
            self.running = False
            self.error_message = _format_backend_error(e)
            raise RuntimeError(f"创建后端失败: {e}") from e

        # 重置各组件状态
        self._gui.clear()
        self._evt_hist.clear()
        self._trig_mat.clear()
        self._proc_mon.reset()

        self.running = True
        self.start_time = time.time()
        self._threads.start()

        # Windows ConPTY 后端需要等待读者线程就绪后才能安全写输入
        # （事件同步替代固定 sleep，就绪即返回，无多余等待）
        if IS_WINDOWS:
            self._threads.wait_reader_ready(timeout=1.0)

    def stop(self, timeout: float = 3.0):
        """停止会话：强杀进程树 + 关闭后端 + 等待读者线程退出

        Args:
            timeout: 等待读者线程退出的超时秒数。
        """
        self.running = False
        self._threads.stop_event.set()
        self._trig_mat.event.set()
        self._proc_mon.crash_event.set()
        # 状态已发布，唤醒可能正阻塞在等待循环里的请求线程
        self._wake.notify()

        # 关闭前获取退出码
        if self._pty and self.exit_code is None:
            self._update_exit_info()

        if self._pty:
            try:
                self._pty.remove_tree()
            except Exception as e:
                _logger.warning("强杀进程树时异常: %s", e)
            try:
                self._pty.close()
            except Exception as e:
                _logger.warning("关闭后端时异常: %s", e)
            self._pty = None
        self._threads.stop(timeout)

    # ════════════════════════════════════════════════════════════
    # I/O
    # ════════════════════════════════════════════════════════════

    def write_input(self, data):
        """写入输入到后端

        统一使用 UTF-8 编码输入的字符串。

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
        if isinstance(data, str):
            data = data.encode("utf-8")
        try:
            self._pty.write(data)
        except Exception as e:
            _logger.error("写入输入失败 (会话 '%s'): %s", self.id, e)
            raise RuntimeError(f"写入输入失败: {e}") from e

    # ── 文本读取（游标/全量/可见屏幕）──

    def mark_cursor(self):
        """将内部游标定位到当前输出末尾（下一次增量输出起点）"""
        self._out_buf.mark_cursor()

    def reset_cursor(self):
        """将内部游标复位到 0（下一次读取为全量输出）"""
        self._out_buf.reset_cursor()

    @property
    def cursor(self) -> int:
        """当前内部游标（历史行索引）"""
        return self._out_buf.cursor

    def get_output_since_cursor(self) -> str:
        """游标之后的增量输出文本"""
        return self._out_buf.get_since_cursor()

    def get_output_visible(self) -> str:
        """可见屏幕文本（pty 模式）；subprocess 模式返回完整缓冲"""
        return self._out_buf.get_visible()

    def get_output_full(self) -> str:
        """全量输出文本（滚动历史 + 可见屏幕）"""
        return self._out_buf.get_full()

    def get_output_lines(self) -> List[str]:
        """全部完整输出行（含未完成尾部作为最后一行）"""
        return self._out_buf.get_all_lines()

    @property
    def mode(self) -> str:
        """后端模式："subprocess" / "pty" """
        return self._mode

    @property
    def pty_type(self) -> str:
        """当前会话使用的后端类型标识"""
        return self._pty.get_type() if self._pty else "none"

    # ════════════════════════════════════════════════════════════
    # 触发条件
    # ════════════════════════════════════════════════════════════

    def wait_for_initial_output(self, timeout: float = 1.0) -> bool:
        """等待首个输出数据到达

        Args:
            timeout: 等待超时（秒）。

        Returns:
            True 表示已收到首个输出。
        """
        return self._out_buf.first_output_event.wait(timeout)

    def set_trigger(
        self,
        pattern: str,
        newline: bool = False,
        fresh: bool = False,
        start_idx: Optional[int] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
    ):
        """设置触发条件（基于输出文本）

        Args:
            pattern:              正则表达式模式。
            newline:              仅在出现新行后才检查触发条件。
            fresh:                新鲜模式 —— 跳过即时检查，等待新数据到达后才开始匹配。
            start_idx:            扫描起始行索引。None 表示从当前缓冲区末尾开始。
            idle_timeout:         输出静默超时（秒）。
            idle_after_first_output: 是否在首次输出后才开始检测静默超时。
        """
        # 触发状态写入与 reader 的 prepare_snapshot 共享 out_buf.lock，
        # 须在持锁上下文中发布，避免 reader 在状态就绪前提前匹配（竞态）。
        with self._out_buf.lock:
            # GUI 返回条件与触发条件在同一时刻建立基线：丢弃后台监控线程
            # 在上一轮遗留的"新窗口"边沿，使本轮只响应基线之后弹出的窗口。
            # 与 -t 只看游标之后的新输出完全对称 —— 两者平级竞争，谁先命中
            # 谁先返回，不存在任何一方让位。
            self._gui.arm()
            self._trig_mat.set(
                pattern=pattern, newline=newline, fresh=fresh,
                start_idx=start_idx,
                idle_timeout=idle_timeout,
                idle_after_first_output=idle_after_first_output,
                line_count=self._out_buf.line_count,
            )
            if fresh:
                self._trig_mat.fresh_cycle = self._out_buf.read_cycle
                return

            # 锁内：记录当前行数（newline 模式基准）+ 提取匹配快照（不执行耗时正则）
            self._trig_mat.newline_count = self._out_buf.line_count
            snapshot = self._trig_mat.prepare_snapshot(self._out_buf)
        # 锁外：执行耗时正则匹配
        if snapshot is not None:
            self._trig_mat.check_snapshot(snapshot)

    def wait_for_trigger(
        self,
        timeout: Optional[float] = None,
    ):
        """等待任一返回条件命中

        返回条件彼此平级、共同竞争，**谁先命中谁先返回**，不存在让位关系：

        - ``matched``        ：``-t`` 正则命中本轮基线之后的新输出
        - ``gui_detected``   ：基线之后子进程弹出新 GUI 窗口
        - ``crashed``        ：进程树检测到崩溃
        - ``ended``          ：子进程正常退出
        - ``idle_timeout``   ：输出静默超时
        - ``timeout``        ：等待超时

        Args:
            timeout: 等待超时（秒）。None 表示无限等待。

        Returns:
            (matched, reason) 元组；``matched`` 仅在触发条件命中时为 True。
        """
        if self._trig_mat.matched:
            return True, "matched"
        if self._proc_mon.crash_event.is_set():
            self._proc_mon.clear_crash()
            return False, "crashed"
        if not self.running:
            return False, "ended"
        if self._gui.consume_detection():
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

            # 复位必须在判定之前：生产者遵循"先发布状态再 notify"，故复位后
            # 读到的即是最新状态；复位与等待之间到达的 notify 会让等待立即返回。
            self._wake.reset()

            if self._trig_mat.matched:
                _logger.info("wait_for_trigger: MATCHED id=%r pattern=%r",
                             self.id, self._trig_mat.pattern)
                return True, "matched"

            if self._trig_mat.check_idle_timeout():
                _logger.info("wait_for_trigger: IDLE_TIMEOUT id=%r "
                             "idle_timeout=%s",
                             self.id, self._trig_mat.idle_timeout)
                return False, "idle_timeout"

            if self._proc_mon.crash_event.is_set():
                self._proc_mon.clear_crash()
                return False, "crashed"

            if not self.running:
                return False, "ended"

            # GUI：事件通道无节流取走（hook 检出即发现），兜底扫描按 1s 门控
            self._gui.drain_events(self._pty)
            now = time.time()
            if now - _last_gui_check >= 1.0:
                _last_gui_check = now
                self._gui.check(self._pty, self.id)
            if self._gui.consume_detection():
                _logger.info("wait_for_trigger: GUI_DETECTED id=%r windows=%d",
                             self.id, len(self._gui.get_gui_windows()))
                return False, "gui_detected"

            # 0.1s 仅是漏醒安全网；任一生产者 notify 都会立刻唤醒本等待
            self._wake.wait(min(0.1, remaining))

    def clear_trigger(self):
        """清除本轮返回条件（触发正则 + 崩溃/GUI 边沿）"""
        _logger.info("clear_trigger: id=%r pattern=%r matched=%s",
                     self.id, self._trig_mat.pattern,
                     self._trig_mat.matched)
        self._trig_mat.clear()
        self._proc_mon.clear_crash()
        self._gui.arm()

    # ── 读者退出回调 ─────────────────────────────────────────

    def _on_reader_exit(self, exit_code, error_message):
        """读者线程退出回调：更新退出信息、关闭后端、通知等待方"""
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
        # running=False 已发布：唤醒阻塞中的等待循环，立刻以 "ended" 返回
        self._wake.notify()
        if self._pty:
            try:
                self._pty.close()
            except Exception as e:
                _logger.warning("关闭后端异常 (会话 '%s'): %s", self.id, e)

    # ── 退出码获取（供 stop() 使用）──────────────────────────

    def _update_exit_info(self):
        """获取子进程退出信息（退出码和错误消息）"""
        if not self._pty:
            return
        code = _capture_exit_code_retry(self._pty)
        if code is not None:
            self.exit_code = code
            if code != 0:
                self.error_message = _format_exit_code_message(code)
        else:
            self.exit_code = None

    def close_window(self, hwnd: int) -> bool:
        """关闭指定 GUI 窗口"""
        if not self._pty:
            return False
        return self._pty.close_gui_window(hwnd)

    # ════════════════════════════════════════════════════════════
    # 事件管理（委托给 EventHistoryManager）
    # ════════════════════════════════════════════════════════════

    def consume_events(self) -> List[dict]:
        """消费所有待处理事件并移入历史记录"""
        return self._evt_hist.consume_all()

    @property
    def pending_event_count(self) -> int:
        """待处理事件数量"""
        return self._evt_hist.pending_count

    # ════════════════════════════════════════════════════════════
    # 子组件公开访问（测试 / handler 通过 @property 获取子组件引用）
    # ════════════════════════════════════════════════════════════

    @property
    def output_buffer(self) -> "OutputBuffer":
        """底层输出缓冲区"""
        return self._out_buf

    @property
    def trigger_matcher(self) -> "TriggerMatcher":
        """底层触发匹配器"""
        return self._trig_mat

    @property
    def event_history(self) -> "EventHistoryManager":
        """底层事件历史管理器"""
        return self._evt_hist

    @property
    def process_monitor(self) -> "ProcessMonitor":
        """底层进程监控器"""
        return self._proc_mon

    @property
    def pipeline(self):
        """底层输出管线（测试用）"""
        return self._pipeline

    # ════════════════════════════════════════════════════════════
    # 状态代理（保持外部接口不变）
    # ════════════════════════════════════════════════════════════

    @property
    def gui_windows(self) -> List[dict]:
        """已检测到的 GUI 窗口列表"""
        return self._gui.get_gui_windows()

    @gui_windows.setter
    def gui_windows(self, value: List[dict]):
        self._gui.gui_windows = value

    @property
    def processes(self) -> List[int]:
        """当前进程树 PID 列表"""
        return self._gui.get_processes()

    @processes.setter
    def processes(self, value: List[int]):
        self._gui.processes = value
