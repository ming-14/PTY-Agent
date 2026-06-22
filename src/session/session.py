"""核心 Session — PTY 会话管理（精简协调器版本）

管理一个交互式子进程的生命周期，通过组合模式将职责委派给：
- OutputBuffer        线程安全输出缓冲区
- TriggerMatcher      触发条件匹配与空闲超时检测
- ProcessMonitor      进程树 diff、IOCP 排空、崩溃检测
- EventHistoryManager 事件队列与历史记录管理
- EncodingDetector    编码探测与解码状态管理
- GuiDetector         GUI 窗口轮询检测
- SessionThreads      后台读者线程与监控线程管理

Session 自身仅保留：PTY 生命周期、I/O 接口、触发条件协调、退出码捕获。
外部访问子组件请通过公开 @property：session.output_buffer / trigger_matcher
/ event_history / process_monitor。
"""

import os
import sys
import errno
import time
import logging
import threading
from typing import Optional, List, Tuple

from ..pty.factory import create_pty
from ..pty.base import PseudoTerminal
from ..config import IS_WINDOWS
from ..config import (
    MAX_OUTPUT_BUFFER,
    MAX_TRIGGER_SCAN,
)
from .process import (
    _get_process_name,
    _get_process_path,
    _format_exit_code_message,
    _signal_name,
    _format_pty_error,
    ProcessMonitor,
    GuiDetector,
)
from .output import (
    OutputBuffer,
    TriggerMatcher,
    safe_regex_search,
    EventHistoryManager,
    PendingEvent,
    _events_to_dicts,
)
from .encoding import EncodingDetector
from .session_threads import SessionThreads, SessionComponents, _capture_exit_code_retry

if IS_WINDOWS:
    from ..pty.windows.error_msg import (
        STILL_ACTIVE, translate_windows_error,
        format_process_exit_code,
    )

_logger = logging.getLogger("pty-session")


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
        cols: int = 80,
        rows: int = 24,
        encoding: Optional[str] = None,
        shell: Optional[str] = None,
    ):
        self.id = session_id
        self.command = command
        self.running = False
        self._shell = shell  # 指定解释器，None=cmd.exe
        self.start_time: float = 0.0  # 会话启动时间戳（Unix 时间）
        self.exit_code = None
        self.error_message = None

        # ── 编码探测 ──
        self._enc = EncodingDetector(encoding)

        # ── 子组件（使用不冲突的内部名，避免 __getattr__ 名称干扰）──
        self._out_buf = OutputBuffer(max_size=MAX_OUTPUT_BUFFER)
        self._trig_mat = TriggerMatcher(decode_func=self._decode_only)
        self._evt_hist = EventHistoryManager()
        self._proc_mon = ProcessMonitor(
            pty_provider=lambda: self._pty,
            event_sink=self._evt_hist.add_event,
        )
        self._gui = GuiDetector(event_sink=self._evt_hist.add_event)
        self._threads = SessionThreads(SessionComponents(
            pty_provider=lambda: self._pty,
            out_buf=self._out_buf,
            trig_mat=self._trig_mat,
            proc_mon=self._proc_mon,
            gui_detector=self._gui,
            session_id=session_id,
            on_exit=self._on_reader_exit,
        ))

        # PTY
        self._pty: Optional[PseudoTerminal] = None

        # 终端尺寸
        self._cols = cols
        self._rows = rows

    # ════════════════════════════════════════════════════════════
    # 生命周期
    # ════════════════════════════════════════════════════════════

    def start(self):
        """启动会话：创建 PTY 后端 + 启动后台读者线程和监控线程"""
        if self.running:
            return
        try:
            self._pty = create_pty(
                self.command, self._cols, self._rows, shell=self._shell)
        except Exception as e:
            self.running = False
            self.error_message = _format_pty_error(e)
            raise RuntimeError(f"创建伪终端失败: {e}") from e

        # 重置各组件状态
        self._gui.clear()
        self._evt_hist.clear()
        self._trig_mat.clear()
        self._proc_mon.reset()
        # 初始化进程快照
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

        # Windows ConPTY 后端需要短暂等待读者线程就绪
        if IS_WINDOWS:
            time.sleep(0.1)

    def stop(self, timeout: float = 3.0):
        """停止会话：关闭 PTY + 等待读者线程退出

        Args:
            timeout: 等待读者线程退出的超时秒数。
        """
        self.running = False
        self._threads.stop_event.set()
        self._trig_mat.event.set()
        self._proc_mon.crash_event.set()

        # 关闭前获取退出码
        if self._pty and self.exit_code is None:
            self._update_exit_info()

        if self._pty:
            try:
                self._pty.close()
            except Exception as e:
                _logger.warning("关闭伪终端时异常: %s", e)
            self._pty = None
        self._threads.stop(timeout)

    # ════════════════════════════════════════════════════════════
    # I/O
    # ════════════════════════════════════════════════════════════

    def write_input(self, data):
        """写入输入到 PTY

        当 data 为 str 且会话已锁定编码时，用该编码编码后写入。

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
        if isinstance(data, str) and self._encoding_locked and self.encoding:
            enc_norm = self.encoding.lower().replace("-", "").replace("_", "")
            if enc_norm not in ("utf8", "utf"):
                _logger.debug("write_input: encoding=%s → encode input to %s",
                              self.encoding, self.encoding)
                data = data.encode(self.encoding, errors="replace")
        try:
            self._pty.write(data)
        except Exception as e:
            _logger.error("写入输入失败 (会话 '%s'): %s", self.id, e)
            raise RuntimeError(f"写入输入失败: {e}") from e

    def get_output(
        self,
        from_offset: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> str:
        """获取会话输出

        Args:
            from_offset: 从指定字节偏移开始读取。None 表示从头读取。
            encoding:    指定解码编码。None 表示使用已探测编码或自动探测。

        Returns:
            解码后的输出文本字符串。
        """
        data = self._out_buf.get_slice(
            start=from_offset if from_offset is not None else 0)
        return self._enc.detect_decode(data, encoding)

    @property
    def output_offset(self) -> int:
        """当前输出缓冲区的总字节长度（用于增量追踪）"""
        return self._out_buf.length

    @property
    def pty_type(self) -> str:
        """当前会话使用的 PTY 后端类型"""
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
        start_offset: Optional[int] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
    ):
        """设置触发条件

        Args:
            pattern:              正则表达式模式。
            newline:              仅在换行后才检查触发条件。
            fresh:                新鲜模式 —— 跳过即时检查，等待新数据到达后才开始匹配。
            start_offset:         扫描起始偏移量。None 表示从当前缓冲区末尾开始。
            idle_timeout:         输出静默超时（秒）。
            idle_after_first_output: 是否在首次输出后才开始检测静默超时。
        """
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
        """等待触发条件命中（GUI 窗口检测和崩溃检测持续生效）

        Args:
            timeout:           等待超时（秒）。None 表示无限等待。
            gui_short_circuit: 是否在检测到 GUI 窗口时提前返回。
                               设为 False 可禁用 GUI 检测的提前中断，
                               事件仍会记录到事件历史。

        Returns:
            (matched, reason) 元组。
        """
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
        """清除触发条件"""
        _logger.info("clear_trigger: id=%r pattern=%r matched=%s",
                     self.id, self._trig_mat.pattern,
                     self._trig_mat.matched)
        self._trig_mat.clear()
        self._proc_mon.clear_crash()

    # ── 编码委托 ─────────────────────────────────────────────

    def _decode_only(self, data: bytes) -> str:
        """无副作用解码（供 TriggerMatcher 回调使用）"""
        return self._enc.decode_only(data)

    # ── 读者退出回调 ─────────────────────────────────────────

    def _on_reader_exit(self, exit_code, error_message):
        """读者线程退出回调：更新退出信息、关闭 PTY、通知等待方"""
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
        if self._pty:
            try:
                self._pty.close()
            except Exception as e:
                _logger.warning("关闭 PTY 异常 (会话 '%s'): %s", self.id, e)

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

    def get_all_events(self, last: Optional[int] = None,
                       since: Optional[float] = None,
                       until: Optional[float] = None) -> List[dict]:
        """获取所有事件（待处理 + 历史），支持过滤"""
        return self._evt_hist.get_all(last=last, since=since, until=until)

    @staticmethod
    def _events_to_dicts(events: List[PendingEvent]) -> List[dict]:
        """将 PendingEvent 对象列表转为字典列表（静态方法，用于测试兼容）"""
        return _events_to_dicts(events)

    def check_event_existence(self, ev: dict) -> bool:
        """检测事件关联的进程/窗口是否仍然存在"""
        return self._evt_hist.check_existence(
            ev, pty_provider=lambda: self._pty)

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

    # ════════════════════════════════════════════════════════════
    # 状态代理（保持外部接口不变）
    # ════════════════════════════════════════════════════════════

    @property
    def encoding(self) -> Optional[str]:
        """当前自动探测到的编码"""
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
        """已检测到的 GUI 窗口列表"""
        return self._gui.gui_windows

    @gui_windows.setter
    def gui_windows(self, value: List[dict]):
        self._gui.gui_windows = value

    @property
    def processes(self) -> List[int]:
        """当前进程树 PID 列表"""
        return self._gui.processes

    @processes.setter
    def processes(self, value: List[int]):
        self._gui.processes = value
        return self._gui.processes

    @processes.setter
    def processes(self, value: List[int]):
        self._gui.processes = value
