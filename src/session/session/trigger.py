"""Session 触发混入 — TriggerMixin

负责触发条件匹配与等待循环：增量触发、快照触发、空闲超时、GUI 短路返回。
输入/输出见 io.py / output.py。
所有方法均通过 Session 实例访问子组件（见 session.py 的 __init__）。
"""

from ...logging import get_logger
from ...protocol.reasons import Reason
import threading
import time
from typing import Optional

_logger = get_logger("pty-session")


class TriggerMixin:
    """触发条件与等待（会话组合的触发部分）"""

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
        err_buf = getattr(self, "_err_buf", None)
        self._trig_mat.set(
            pattern=pattern,
            newline=newline,
            fresh=fresh,
            start_offset=start_offset,
            idle_timeout=idle_timeout,
            idle_after_first_output=idle_after_first_output,
            buffer_length=self._out_buf.length,
            err_buffer=err_buf,
            err_buffer_length=err_buf.length if err_buf is not None else None,
        )
        if fresh:
            self._trig_mat.fresh_cycle = self._out_buf.read_cycle
            if err_buf is not None:
                self._trig_mat.fresh_cycle_err = err_buf.read_cycle
            return

        self._trig_mat.newline_count = self._out_buf.count_byte(ord("\n"))
        with self._out_buf.lock:
            self._trig_mat.check(self._out_buf)
        # 子进程模式双流：stderr 中已就位但早于 set 的内容（如 Python 交互
        # 提示符/banner）在后续新 chunk 到达前不会被再次扫描，需在此补一次检查
        if err_buf is not None:
            with err_buf.lock:
                self._trig_mat.check(err_buf)

    def wait_for_trigger(
        self,
        timeout: Optional[float] = None,
        gui_short_circuit: bool = True,
        cancel_event: Optional[threading.Event] = None,
    ):
        """等待触发条件命中（取消可经 cancel_event 中断，返回 reason=cancelled）"""
        host = self.plugin_host
        host.enter_wait()
        try:
            return self._wait_for_trigger_inner(timeout, gui_short_circuit, cancel_event)
        finally:
            host.exit_wait()

    def _wait_for_trigger_inner(
        self,
        timeout: Optional[float] = None,
        gui_short_circuit: bool = True,
        cancel_event: Optional[threading.Event] = None,
    ):
        """触发等待主体循环（wait_for_trigger 在 enter/exit_wait 包裹下调用）

        每轮循环检查插件返回请求（request_return），命中立即返回，
        原因由插件自定义并原样透传；cancel_event 置位时以 reason=cancelled 返回。
        迭代骨架（cancel/remaining/timeout/循环）复用统一等待引擎 wait_reason，
        检查顺序与事件等待原语保持原样（行为零变化）。
        """
        if self._trig_mat.matched:
            return True, Reason.MATCHED
        if self._proc_mon.crash_event.is_set() and self._is_real_crash():
            self._proc_mon.clear_crash()
            return False, Reason.CRASHED
        if not self.running:
            return False, self.resolve_exit_reason()
        if (
            gui_short_circuit
            and self._gui.gui_windows
            and self._gui.detected_event.is_set()
        ):
            self._gui.detected_event.clear()
            return False, Reason.GUI_DETECTED

        from ..wait import NO_RETURN, wait_reason

        deadline = time.time() + (timeout if timeout is not None else 999999.0)
        _last_gui_check = 0.0

        def _iteration(remaining):
            nonlocal _last_gui_check
            if self._trig_mat.check_idle_timeout():
                _logger.info(
                    "wait_for_trigger: IDLE_TIMEOUT id=%r idle_timeout=%s",
                    self.id,
                    self._trig_mat.idle_timeout,
                )
                return False, Reason.IDLE_TIMEOUT

            plugin_reason = self.plugin_host.consume_return_request()
            if plugin_reason:
                _logger.info(
                    "wait_for_trigger: PLUGIN_RETURN id=%r reason=%r",
                    self.id,
                    plugin_reason,
                )
                return True, plugin_reason

            if self._proc_mon.crash_event.is_set() and self._is_real_crash():
                self._proc_mon.clear_crash()
                return False, Reason.CRASHED

            self._trig_mat.event.wait(min(0.1, remaining))
            if self._trig_mat.matched:
                _logger.info(
                    "wait_for_trigger: MATCHED id=%r pattern=%r",
                    self.id,
                    self._trig_mat.pattern,
                )
                return True, Reason.MATCHED
            if not self.running:
                return False, self.resolve_exit_reason()

            detected, _last_gui_check = self.check_gui_detected(
                _last_gui_check, enabled=gui_short_circuit
            )
            if detected:
                return False, Reason.GUI_DETECTED
            return None

        def _on_cancel():
            _logger.info(
                "wait_for_trigger: CANCELLED id=%r pattern=%r",
                self.id,
                self._trig_mat.pattern,
            )

        def _on_timeout():
            _logger.info(
                "wait_for_trigger: TIMEOUT id=%r pattern=%r timeout=%s",
                self.id,
                self._trig_mat.pattern,
                timeout,
            )
            return False, Reason.TIMEOUT

        result = wait_reason(
            deadline=deadline,
            cancel_event=cancel_event,
            iteration=_iteration,
            on_timeout=_on_timeout,
            on_cancel=_on_cancel,
        )
        if result is NO_RETURN:
            return False, Reason.TIMEOUT
        return result

    def check_gui_detected(self, last_check_time: float, enabled: bool = True) -> tuple:
        """GUI 窗口检测（节流 1s）— 等待循环统一判定点

        enabled=False（子进程 trigger 的 gui_short_circuit=False）时仍保留
        节流轮询（对齐既有时序），但不清空事件也不对外报告。
        返回 (检测到新窗口, 本次检测时刻)；检测到即清空事件，供上层返回 gui_detected。
        """
        if self._gui is None:
            return False, last_check_time
        now = time.time()
        if now - last_check_time < 1.0:
            return False, last_check_time
        try:
            self._gui.check(getattr(self, "_tracker", None), self.id)
        except Exception:
            pass
        detected = bool(self._gui.gui_windows and self._gui.detected_event.is_set())
        if detected and enabled:
            self._gui.detected_event.clear()
            return True, now
        return False, now

    def clear_trigger(self):
        _logger.info(
            "clear_trigger: id=%r pattern=%r matched=%s",
            self.id,
            self._trig_mat.pattern,
            self._trig_mat.matched,
        )
        self._trig_mat.clear()
        self._proc_mon.clear_crash()

    def set_snapshot_trigger(
        self,
        pattern: Optional[str] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
    ):
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
