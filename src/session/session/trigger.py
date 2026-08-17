"""Session 触发混入 — TriggerMixin

负责触发条件匹配与等待循环：增量触发、快照触发、空闲超时、GUI 短路返回。
输入/输出见 io.py / output.py。
所有方法均通过 Session 实例访问子组件（见 session.py 的 __init__）。
"""

from ...logging import get_logger
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
        self._trig_mat.set(
            pattern=pattern,
            newline=newline,
            fresh=fresh,
            start_offset=start_offset,
            idle_timeout=idle_timeout,
            idle_after_first_output=idle_after_first_output,
            buffer_length=self._out_buf.length,
        )
        if fresh:
            self._trig_mat.fresh_cycle = self._out_buf.read_cycle
            return

        self._trig_mat.newline_count = self._out_buf.count_byte(ord("\n"))
        with self._out_buf.lock:
            self._trig_mat.check(self._out_buf)

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
        """
        if self._trig_mat.matched:
            return True, "matched"
        if self._proc_mon.crash_event.is_set() and self._is_real_crash():
            self._proc_mon.clear_crash()
            return False, "crashed"
        if not self.running:
            return False, "crashed" if self._is_real_crash() else "ended"
        if (
            gui_short_circuit
            and self._gui.gui_windows
            and self._gui.detected_event.is_set()
        ):
            self._gui.detected_event.clear()
            return False, "gui_detected"

        deadline = time.time() + (timeout if timeout is not None else 999999.0)
        _last_gui_check = 0.0
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _logger.info(
                    "wait_for_trigger: CANCELLED id=%r pattern=%r",
                    self.id,
                    self._trig_mat.pattern,
                )
                return False, "cancelled"

            remaining = deadline - time.time()
            if remaining <= 0:
                _logger.info(
                    "wait_for_trigger: TIMEOUT id=%r pattern=%r timeout=%s",
                    self.id,
                    self._trig_mat.pattern,
                    timeout,
                )
                return False, "timeout"

            if self._trig_mat.check_idle_timeout():
                _logger.info(
                    "wait_for_trigger: IDLE_TIMEOUT id=%r idle_timeout=%s",
                    self.id,
                    self._trig_mat.idle_timeout,
                )
                return False, "idle_timeout"

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
                return False, "crashed"

            self._trig_mat.event.wait(min(0.1, remaining))
            if self._trig_mat.matched:
                _logger.info(
                    "wait_for_trigger: MATCHED id=%r pattern=%r",
                    self.id,
                    self._trig_mat.pattern,
                )
                return True, "matched"
            if not self.running:
                return False, "crashed" if self._is_real_crash() else "ended"

            now = time.time()
            if now - _last_gui_check >= 1.0:
                _last_gui_check = now
                self._gui.check(self._tracker, self.id)
            if gui_short_circuit and self._gui.detected_event.is_set():
                self._gui.detected_event.clear()
                return False, "gui_detected"

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
        newline: bool = False,
    ):
        self._trig_mat.set_snapshot_trigger(
            pattern=pattern,
            idle_timeout=idle_timeout,
            idle_after_first_output=idle_after_first_output,
            newline=newline,
        )

    def check_snapshot_trigger(self, snapshot_text: str) -> bool:
        return self._trig_mat.check_snapshot(snapshot_text)

    def check_snapshot_idle_timeout(self) -> bool:
        return self._trig_mat.check_idle_timeout()

    def notify_snapshot_changed(self):
        self._trig_mat.notify_snapshot_changed(time.monotonic())
