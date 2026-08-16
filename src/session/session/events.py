"""Session 事件混入 — EventsMixin

负责会话事件统一入口与事件查询：事件接收/消费/历史、读者退出回调、
退出码捕获。生命周期（start/stop）见 session.py。
所有方法均通过 Session 实例访问子组件（见 session.py 的 __init__）。
"""

import threading
from typing import List, Optional

from ...output import PendingEvent, _events_to_dicts
from ...process import _format_exit_code_message
from .threads import (
    _capture_exit_code_retry,
    _extract_crash_error_from_output,
)
from ...logging import get_logger

_logger = get_logger("pty-session")


class EventsMixin:
    """事件接收与历史（会话组合的事件部分）"""

    def _on_event(self, ev):
        """会话事件统一入口（ProcessMonitor/GuiDetector 的 event_sink）

        插件收到与 events 命令一致的 dict 事件（先过插件链再入事件历史）。
        """
        dicts = _events_to_dicts([ev])
        self.plugin_host.on_event(dicts[0] if dicts else {})
        self._evt_hist.add_event(ev)

    # ── 读者退出回调 ─────────────────────────────────────────

    def _on_all_processes_exited(self):
        if not self.running:
            return
        _logger.info("会话 '%s': 所有子进程已退出，调用 stop", self.id)
        threading.Thread(
            target=self.stop, daemon=True, name=f"pty-stop-{self.id}"
        ).start()

    def _on_reader_exit(self, exit_code, error_message):
        if exit_code is not None:
            self.exit_code = exit_code
            if error_message is not None:
                self.error_message = error_message
        _logger.info(
            "会话 '%s': reader exiting, running=%s, exit_code=%s, error_msg=%s",
            self.id,
            self.running,
            self.exit_code,
            self.error_message,
        )
        self.running = False
        # 会话结束：卸载全部挂载插件（幂等，stop 引发的 reader 退出重复调用无副作用）
        self.plugin_host.detach_all(exit_code)
        self._out_buf.first_output_event.set()
        self._trig_mat.event.set()
        self._publisher.notify_end(self)

    # ── 退出码获取 ────────────────────────────────────────────

    def _update_exit_info(self):
        if not self._pty:
            return
        # 优先取 tracker 已收尸的 root 退出码（Unix 唯一 waitpid 收尸点，
        # 先经其收尸后 pty 的 try_wait 会因进程已回收拿不到退出码）
        try:
            code = self._tracker.get_root_exit_code()
        except Exception:
            code = None
        if code is None:
            code = _capture_exit_code_retry(self._pty)
        else:
            _logger.debug(
                "update_exit_info: sid=%r from tracker exit=%s", self.id, code
            )
        if code is not None:
            self.exit_code = code
            if code != 0:
                stdout_data = self._out_buf.get_slice() if self._out_buf else b""
                extracted = _extract_crash_error_from_output(stdout_data)
                self.error_message = extracted or _format_exit_code_message(code)
        else:
            self.exit_code = None

    # ════════════════════════════════════════════════════════════
    # 事件管理（委托给 EventHistoryManager）
    # ════════════════════════════════════════════════════════════

    def consume_events(self) -> List[dict]:
        return self._evt_hist.consume_all()

    def peek_events(self) -> List[dict]:
        return self._evt_hist.peek_pending()

    def get_all_events(
        self,
        last: Optional[int] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
    ) -> List[dict]:
        return self._evt_hist.get_all(last=last, since=since, until=until)

    @staticmethod
    def _events_to_dicts(events: List[PendingEvent]) -> List[dict]:
        return _events_to_dicts(events)

    def check_event_existence(self, ev: dict) -> bool:
        return self._evt_hist.check_existence(
            ev, tracker_provider=lambda: self._tracker
        )

    @property
    def pending_event_count(self) -> int:
        return self._evt_hist.pending_count
