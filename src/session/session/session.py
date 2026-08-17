"""核心 Session — PTY 会话管理（协调器基类）

管理一个交互式子进程的生命周期，通过组合模式将职责委派给：
- OutputBuffer        线程安全输出缓冲区
- TriggerMatcher      触发条件匹配与空闲超时检测
- ProcessMonitor      进程树 diff、IOCP 排空、崩溃检测
- EventHistoryManager 事件队列与历史记录管理
- EncodingDetector    编码探测与解码状态管理
- GuiDetector         GUI 窗口轮询检测
- Threads             后台读者线程与监控线程管理
- InputInterceptor    SGR 鼠标拦截、键盘 VT 拦截与鼠标动作执行
- SessionPublisher    订阅者与结束回调管理

本文件仅保留 Session 的核心身份：子组件装配（__init__）、生命周期
（start/stop）、子组件公开访问与状态代理。其余职责按功能拆到同包混入类
（MRO 顺序即定义顺序）：
- InputMixin    输入写入/信号/鼠标动作    io.py
- OutputMixin   输出读取/屏幕快照/resize   output.py
- TriggerMixin  触发条件与等待             trigger.py
- EventsMixin   事件接收/历史/退出回调     events.py
- _win_console  Windows Ctrl+C 控制台辅助  _win_console.py

外部访问子组件请通过公开 @property：session.output_buffer / trigger_matcher
/ event_history / process_monitor。
"""

import threading
import time
import uuid
from contextlib import contextmanager
from typing import List, Optional

from ...config.common import DEFAULT_COLS, DEFAULT_ROWS, IS_WINDOWS
from ...config.daemon import MAX_OUTPUT_BUFFER, STOP_TIMEOUT
from ...encoding import EncodingDetector
from ...input import InputInterceptor
from ...output import EventHistoryManager, OutputBuffer, TriggerMatcher
from ...plugins.host import PluginHost
from ...process import (
    GuiDetector,
    ProcessMonitor,
    ProcessTreeTracker,
    _format_pty_error,
    create_process_tree_tracker,
)
from ...pty.base import PseudoTerminal
from ...pty.pty_factory import create_pty
from ...pty.subprocess_pty import SubprocessPseudoTerminal
from ...terminal.screen import TerminalScreen
from ..publisher import SessionPublisher
from .threads import Threads, Components
from .io import InputMixin
from .output import OutputMixin
from .trigger import TriggerMixin
from .events import EventsMixin
from ...logging import get_logger

_logger = get_logger("pty-session")


class Session(
    InputMixin,
    OutputMixin,
    TriggerMixin,
    EventsMixin,
):
    """PTY 会话（协调器）

    管理一个交互式子进程，提供写入输入、读取输出、触发条件检测等功能。
    通过组合模式将具体职责委派给独立的子组件，输入/输出/触发/事件逻辑
    由各 *Mixin 混入类提供（见模块 docstring）。

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
        mode: str = "pty",
        cli_plugins: Optional[list] = None,
    ):
        self.id = session_id
        self.uid = str(uuid.uuid4())
        self.command = command
        self.running = False
        self.mode = mode  # "pty" | "subprocess"
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
        # 子进程模式：stderr 独立缓冲（stdout 用 _out_buf）
        self._err_buf = OutputBuffer(max_size=MAX_OUTPUT_BUFFER) if mode == "subprocess" else None
        # 子进程模式 stderr 已读偏移（增量消费用；与 stdout 的 --offset 语义一致）
        self._stderr_read_offset = 0
        self._trig_mat = TriggerMatcher(decode_func=self._decode_only_len)
        self._evt_hist = EventHistoryManager()
        self._tracker = create_process_tree_tracker()
        # 会话级插件宿主：须在后台线程构造前创建（Components 引用）
        self.plugin_host = PluginHost(self)
        # 会话挂载的 CLI 插件名（exec --plugin 经 cliPlugins 记录；daemon 不加载
        # CLI 插件实例，仅记录供客户端在 read/send/mouse 时自动挂钩回调）
        self.cli_plugin_names = list(cli_plugins or [])
        self._proc_mon = ProcessMonitor(
            tracker=self._tracker,
            event_sink=self._on_event,
        )
        self._gui = GuiDetector(event_sink=self._on_event)
        # 子进程模式无终端屏幕（无快照）；pty 模式保留 TerminalScreen
        self._screen = (
            TerminalScreen(cols=self._cols, rows=self._rows)
            if mode == "pty"
            else None
        )
        self._threads = Threads(
            Components(
                pty_provider=lambda: self._pty,
                out_buf=self._out_buf,
                err_buf=self._err_buf,
                trig_mat=self._trig_mat,
                proc_mon=self._proc_mon,
                tracker=self._tracker,
                gui_detector=self._gui,
                screen=self._screen,
                session_id=session_id,
                on_exit=self._on_reader_exit,
                session_ref=lambda: self,
                plugin_host=self.plugin_host,
                mode=mode,
            )
        )

        self._pty: Optional[PseudoTerminal] = None
        # stop 防重入标志（自然结束路径会在读者线程内二次调用 stop）
        self._stop_started = False
        # stop 完整执行完毕标志（release_components 仅在此为真时释放组件，避免
        # 与外部线程进行中的 stop() 并发导致组件被提前置空）
        self._stop_finished = False
        # hold 引用计数：exec/read/send handler 处理期间持有会话（上下文管理器），
        # 会话自然结束（reader 线程）触发 release 时若仍有 handler 在读取缓冲，
        # 置 pending 等最后一个 hold 退出后实际释放，避免 handler 读到 None。
        self._hold_count = 0
        self._release_pending = False
        self._hold_cond = threading.Condition()
        # 创建期预持有：manager.create_session 在 start 前调用 pre_hold() 进入，
        # 把"create_session 返回 → handler 首次进入 hold"之间的空窗并入持有。
        # 子进程在该空窗内快速退出时，reader 线程走完整套结束生命周期并触发
        # release_components；若无预持有（_hold_count==0），缓冲会被立即释放，
        # handler 随后访问 _out_buf 即 AttributeError。预持有保证空窗内的
        # release 一律转 pending，待首个 hold 退出时才实际释放。
        self._creation_hold = False

        self._publisher = SessionPublisher()

        # 子进程模式无终端输入编码（直接写 stdin）
        self._input_interceptor = None
        self._input_encoder = None
        if mode == "pty":
            self._input_interceptor = InputInterceptor(
                cols=self._cols,
                rows=self._rows,
            )
            # wezterm 模式感知输入编码器：与终端模型共享同一 Terminal 实例，
            # 编码结果直接写 pty
            from ...input.wezterm_input import WeztermInputEncoder

            self._input_encoder = WeztermInputEncoder(self._screen.emulator)

        self.client_config: dict = {}
        self._last_snapshot_lines: Optional[List[str]] = None
        # get_snapshot_diff 内容变化键（feed_count, cols, rows, keep_ansi）
        self._last_snapshot_key: Optional[tuple] = None
        # screenBufferZ 压缩缓存（键 = feed_count/cols/rows；内容未变时复用）
        self._screen_buffer_cache: Optional[tuple] = None
        # 不使用 _suppress_publish_until 抑制 ConPTY repaint：
        # 让 ConPTY 输出直达前端，并通过 snapshot 强制 resync（term.reset + write）。

    # ════════════════════════════════════════════════════════════
    # 生命周期
    # ════════════════════════════════════════════════════════════

    def start(self):
        """启动会话：创建后端 + 启动后台读者线程和监控线程"""
        if self.running:
            return
        self._stop_started = False
        self._stop_finished = False
        try:
            if self.mode == "subprocess":
                self._pty = SubprocessPseudoTerminal(
                    self.command,
                    cwd=self._cwd,
                    env=self._env,
                    tracker=self._tracker,
                )
            else:
                self._pty = create_pty(
                    self.command,
                    self._cols,
                    self._rows,
                    cwd=self._cwd,
                    env=self._env,
                    encoding=self._child_encoding,
                    tracker=self._tracker,
                )
        except Exception as e:
            self.running = False
            self.error_message = _format_pty_error(e)
            raise RuntimeError(f"创建伪终端失败: {e}") from e

        self._gui.clear()
        self._evt_hist.clear()
        self._trig_mat.clear()
        self._proc_mon.reset()
        try:
            pids = self._tracker.get_process_list()
            self._proc_mon.reset(initial_pids=set(pids) if pids else set())
        except Exception:
            self._proc_mon.reset()

        self.running = True
        self.start_time = time.time()
        self._threads.start()

        if IS_WINDOWS:
            time.sleep(0.1)

    def stop(self, timeout: float = STOP_TIMEOUT):
        """停止会话：强杀进程树 + 关闭 PTY + 等待读者线程退出

        防重入：会话自然结束时（读者线程内 _on_reader_exit → notify_end →
        _on_session_ended）会再次进入 stop；若停进程由当前线程发起，
        二次 stop 的 join 会 join 当前线程（Python 3.12+ 直接报错），
        首调即完成全部清理，重入调用直接返回。
        """
        if self._stop_started:
            _logger.debug("stop: sid=%r 已在停止中，跳过重入调用", self.id)
            return
        self._stop_started = True
        import time as _time

        _t0 = _time.monotonic()
        self.running = False
        self._threads.stop_event.set()
        self._trig_mat.event.set()
        self._proc_mon.crash_event.set()

        if self._pty and self.exit_code is None:
            self._update_exit_info()
            _logger.debug(
                "stop: sid=%r _update_exit_info took %.3fs exit=%s",
                self.id,
                _time.monotonic() - _t0,
                self.exit_code,
            )

        _t1 = _time.monotonic()
        try:
            # 显式终止顺序：tracker.kill_tree → pty.close → tracker.close
            self._tracker.kill_tree()
        except Exception as e:
            _logger.warning("强杀进程树时异常: %s", e)
        _logger.debug(
            "stop: sid=%r kill_tree took %.3fs", self.id, _time.monotonic() - _t1
        )
        if self._pty:
            _t2 = _time.monotonic()
            try:
                self._pty.close()
            except Exception as e:
                _logger.warning("关闭伪终端时异常: %s", e)
            self._pty = None
            _logger.debug(
                "stop: sid=%r pty.close took %.3fs", self.id, _time.monotonic() - _t2
            )
        _t3 = _time.monotonic()
        self._threads.stop(timeout)
        try:
            self._tracker.close()
        except Exception as e:
            _logger.warning("关闭进程树追踪器时异常: %s", e)
        _logger.info(
            "stop: sid=%r threads.stop took %.3fs total %.3fs",
            self.id,
            _time.monotonic() - _t3,
            _time.monotonic() - _t0,
        )
        self._stop_finished = True

    @contextmanager
    def hold(self):
        """上下文管理器：handler 处理期间持有本会话（防止缓冲被提前释放）

        会话自然结束后（reader 线程）管理器会调 release_components 释放大缓冲；
        若 exec/read/send handler 仍在处理（等待输出/构建响应），此时缓冲被
        置 None 会崩溃。handler 在处理期间用 with session.hold() 包裹，
        release 遇持有中置 pending，最后一个 hold 退出时执行实际释放。
        """
        self.acquire_hold()
        try:
            yield self
        finally:
            self.release_hold()

    def acquire_hold(self) -> None:
        """持有 +1（hold() 的底层实现，供长生命周期异步路径（web 订阅）使用）

        首个持有若恰逢创建期预持有（_creation_hold）则直接消费预持有，
        不再叠加计数；预持有由此转交给首个持有者。
        """
        with self._hold_cond:
            if self._creation_hold:
                self._creation_hold = False
            else:
                self._hold_count += 1

    def release_hold(self) -> None:
        """持有 -1（hold() 的底层实现，须与 acquire_hold 配对）

        最后一个持有退出且存在 pending 释放时执行实际释放。
        """
        with self._hold_cond:
            self._hold_count -= 1
            do_release = self._hold_count == 0 and self._release_pending
            if do_release:
                self._release_pending = False
        if do_release:
            self._do_release()

    def pre_hold(self) -> None:
        """创建期预持有：start() 前由 manager.create_session 调用

        见 __init__ 中 _creation_hold 的说明：把创建到首个 hold 的
        空窗并入持有，避免子进程在该窗口内快速退出时缓冲被提前释放。
        """
        with self._hold_cond:
            if self._creation_hold:
                return
            self._creation_hold = True
            self._hold_count += 1

    def release_creation_hold(self) -> None:
        """撤销创建期预持有（start 失败路径：会话未交接给 handler）

        预持有被首个 hold 消费后调用本方法无效果；仅当预持有仍存续
        （会话创建失败、从未进入任何 hold）时，归还计数并执行可能的
        pending 释放。
        """
        do_release = False
        with self._hold_cond:
            if not self._creation_hold:
                return
            self._creation_hold = False
            self._hold_count -= 1
            if self._hold_count == 0 and self._release_pending:
                self._release_pending = False
                do_release = True
        if do_release:
            self._do_release()

    def release_components(self) -> None:
        """最终移除时释放会话大缓冲组件（幂等）

        大输出缓冲（_threads._comp.out_buf/err_buf，可达 100MB 级）由
        Threads 链持有；会话结束后该链不再被使用，但会话对象与组件间存在
        bound method 循环引用（TriggerMatcher.decode_func、ProcessMonitor/
        GuiDetector 的 event_sink、Threads 的 on_exit/session_ref 等），
        只能等待 gen2 GC 收集，空闲 daemon 触发频率低导致缓冲滞留。
        由管理器在归档完成、会话从活跃表最终移除后调用：断开 Threads 链
        后大缓冲经引用计数立即回收。其余属性（_gui/_trig_mat/process_monitor
        等）保留：已结束会话的 handler 仍需读取进程/事件元数据，
        其轻量循环由 GC 兜底。
        注意：stop→start 重启生命周期在此之前使用组件，不得提前调用。
        """
        # stop() 可能由其他线程进行中（自然结束回调与外部 stop 并发）：
        # 此时释放会让进行中的 stop() 访问 None 崩溃，仅当 stop 完整
        # 执行完毕后释放；未完成时由 stop 的发起方（remove_session 等）
        # 在 stop 返回后依序调用本方法。
        if not self._stop_finished:
            return
        with self._hold_cond:
            if self._hold_count > 0:
                # 仍有 handler 在处理本会话（读缓冲/构建响应），
                # 延迟到最后一个 hold 退出时实际释放
                self._release_pending = True
                return
            self._release_pending = False
        self._do_release()

    def _do_release(self) -> None:
        # 断开全部大缓冲引用（OutputBuffer 被 Session._out_buf 与
        # Threads._comp.out_buf 双引用持有，双断后引用计数归零立即回收，
        # 无需等待 GC）；归档早已完成，_out_buf/_err_buf 不再被读取
        # （已结束会话的历史输出走 history_store）。
        self._threads = None
        self._out_buf = None
        if self._err_buf is not None:
            self._err_buf = None
        # 断开其余循环引用链与重量级子组件，让已结束会话对象图被引用计数
        # 立即整体回收，不再依赖 gen2 GC：
        # - 循环引用来源：TriggerMatcher.decode_func、ProcessMonitor/GuiDetector
        #   的 event_sink、Threads 的 on_exit/session_ref（已随 _threads 断开）、
        #   PluginHost(session)、tracker 回调、publisher 回调；
        # - 重量级组件：TerminalScreen 滚动缓冲（scrollback 可达数万行）、
        #   事件历史、输入编码器、编码探测器滚动缓存。
        # 此前这些残留循环只能等 gen2 GC 兜底，而空闲 daemon 的 gen2 触发
        # 频率低，残留对象滞留至下次全量 GC，表现为 RSS 随会话累积上行、
        # 空闲期才被后台 GC 逐步清理（黑盒观测：内存随 ended 会话增长且
        # 空闲不回落）。
        # 安全前提：release_components/_do_release 仅在会话停止完成且无任何
        # handler 持有（hold 计数为 0）时执行；此时已结束会话已从管理器
        # 活跃表移除，handler 无法再访问本对象（历史读取走 history_store），
        # 故可安全置空全部组件。
        self._trig_mat = None
        self._proc_mon = None
        self._gui = None
        self._tracker = None
        self._publisher = None
        self.plugin_host = None
        self._input_interceptor = None
        self._input_encoder = None
        self._screen = None
        self._evt_hist = None
        self._enc = None
        self._last_snapshot_lines = None
        self._last_snapshot_key = None
        self._screen_buffer_cache = None

    # ── 编码委托 ─────────────────────────────────────────────

    def _decode_only_len(self, data: bytes) -> tuple:
        """仅解码并返回 (文本, 消费字节数)（供 TriggerMatcher 滚动缓存使用）"""
        return self._enc.decode_only_len(data)

    # ════════════════════════════════════════════════════════════
    # 状态代理
    # ════════════════════════════════════════════════════════════

    @property
    def output_offset(self) -> int:
        return self._out_buf.length

    @property
    def err_output_offset(self) -> int:
        """stderr 缓冲区字节长度（仅子进程模式）"""
        return self._err_buf.length if self._err_buf else 0

    @property
    def stderr_read_offset(self) -> int:
        """已增量交付的 stderr 字节偏移（仅子进程模式）

        随 read_new_err_output 推进；与 stdout 的 outputOffset 语义一致，
        表示 read 返回的 stderrOutput 内容在 stderr 缓冲中的结束字节位置。
        """
        return self._stderr_read_offset

    @property
    def pty_type(self) -> str:
        return self._pty.get_type() if self._pty else "none"

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def cwd(self) -> Optional[str]:
        return self._cwd

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

    def close_window(self, hwnd: int) -> bool:
        return self._tracker.close_gui_window(hwnd)

    def get_pty_process_list(self) -> list:
        try:
            return self._tracker.get_process_list()
        except Exception:
            return []

    def get_pty_child_pid(self):
        if self._pty:
            return self._pty.get_child_pid()
        return None

    # ── 子进程模式 stderr 访问 ────────────────────────────────

    def get_err_output(
        self,
        from_offset: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> str:
        """获取 stderr 输出（仅子进程模式；pty 模式返回空）"""
        if self._err_buf is None:
            return ""
        data = self._err_buf.get_slice(
            start=from_offset if from_offset is not None else 0
        )
        return self._enc.detect_decode(data, encoding)

    def get_err_output_with_offset(
        self,
        from_offset: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> tuple:
        """原子获取 stderr 输出及当前偏移（仅子进程模式）"""
        if self._err_buf is None:
            return "", 0
        data, cur_offset = self._err_buf.get_slice_with_length(
            start=from_offset if from_offset is not None else 0
        )
        return self._enc.detect_decode(data, encoding), cur_offset

    def read_new_err_output(self, encoding: Optional[str] = None) -> str:
        """增量读取 stderr：返回自上次读取以来的新增内容并推进偏移

        stdout 由调用方传 --offset 增量读取；stderr 无独立 offset 参数，
        故由会话自身记录已读偏移（_stderr_read_offset），每次 CLI 命令
        只返回新增 stderr，stderrOutputOffset 反映已读位置。

        Returns:
            新增 stderr 文本；无新增或非子进程模式返回空串。
        """
        if self._err_buf is None:
            return ""
        start = self._stderr_read_offset
        data, cur_offset = self._err_buf.get_slice_with_length(start=start)
        self._stderr_read_offset = cur_offset
        return self._enc.detect_decode(data, encoding)

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
    def tracker(self) -> ProcessTreeTracker:
        return self._tracker

    @property
    def publisher(self) -> "SessionPublisher":
        return self._publisher
