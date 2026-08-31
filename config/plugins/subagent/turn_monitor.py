"""TurnMonitor — 子代理回合状态监控器

每会话一个后台线程，轮询屏幕 ai_status 检测回合结束 / 卡权限，
通过 EventBus 发布事件供插件内部订阅（wait 等）使用。
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Optional

from .parser_loader import import_parser


class TurnMonitor:
    """子代理回合状态监控器（每会话一个）

    启动后后台轮询屏幕 ai_status，状态变化时通过 EventBus 发布事件：
    - subagent.turn_complete  — 回合完成（working → idle，或 idle 但屏幕内容变化）
    - subagent.turn_start    — 新回合开始（idle → working）
    - subagent.awaiting_approval — 卡在权限确认

    按 agent 类型选择对应的 screen parser（workbuddyparser / devinparser）。
    """

    _BUSY_STATUSES = frozenset(("thinking", "tool_running", "asking"))

    def __init__(self, session, events, notify_manager=None, poll_interval: float = 1.0,
                 agent: str = "", display_name: str = "", stuck_timeout: float = 20.0):
        self._session = session
        self._events = events
        self._notify_mgr = notify_manager
        self._poll = poll_interval
        self._stuck_timeout = stuck_timeout  # 屏幕静默超过此秒数 → 程序未反应
        self._agent = agent
        self._display_name = display_name
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_status: Optional[str] = None
        self._last_hash: Optional[str] = None
        self._notified: set = set()
        self.last_complete: Optional[dict] = None
        self.feedback_pending = True
        self._unknown_count = 0
        # 程序未反应检测状态
        self._last_change_ts: Optional[float] = None  # 屏幕最后变化时间
        self._stuck_notified = False                   # 当前反馈周期是否已发未反应
        self._prev_fb = True                           # 上一轮 feedback_pending 值（边沿检测）
        # 首次 busy 门控：仅当见过 busy 状态（thinking/tool_running/asking）后，
        # idle→idle 内容变化才触发 turn_complete——启动阶段（信任对话框等）
        # 屏幕从欢迎页切到对话框属 idle→idle 变化，未见过 busy 不判完成，
        # 避免假阳性 turn_complete 吞掉真实回合完成通知。
        self._seen_busy = False
        # 启动宽限期：监控启动后该秒数内，未见过 busy 的 idle→idle 变化
        # 视为启动阶段切换不判完成；超过后视为真实回合完成（快速轮兜底）。
        # 信任对话框/欢迎页切换通常 < 3s，快速轮完成约 10s，6s 取两者之间。
        self._startup_grace = 6.0
        self._started_at = time.monotonic()

    def _past_startup_grace(self) -> bool:
        """是否已过启动宽限期（监控启动后 _startup_grace 秒）"""
        return time.monotonic() - self._started_at > self._startup_grace

    def _notify_detail(self, kind: str) -> str:
        return {
            "turn_complete": f"SubAgent: {self._display_name}已完成",
            "awaiting_approval": f"SubAgent: {self._display_name}需要您的审批",
            "asking": f"SubAgent: {self._display_name}需要您的回复",
            "unknown": f"SubAgent: {self._display_name}状态无法解析",
            "stuck": f"SubAgent: {self._display_name}程序未反应",
        }.get(kind, "")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name=f"turn-monitor-{self._session.id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            screen_mod = import_parser(self._agent, "adapters.screen")
        except Exception:
            _log().warning("TurnMonitor: screen parser 导入失败，monitor 不启动")
            return

        while not self._stop.is_set():
            try:
                if not self._session.running:
                    break
                vt = self._session.get_snapshot(keep_ansi=True)
                if not vt:
                    self._stop.wait(self._poll)
                    continue
                live = screen_mod.parse_screen_snapshot(vt)
                status = live.ai_status
                screen_hash = self._screen_hash()
                self._check_transition(status, screen_hash)
                now = time.time()
                self._check_stuck(screen_hash, now)
                self._last_status = status
                self._last_hash = screen_hash
            except Exception:
                pass
            self._stop.wait(self._poll)

    def _screen_hash(self) -> str:
        try:
            text = self._session.get_snapshot(keep_ansi=False) or ""
            return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()
        except Exception:
            return ""

    def _check_transition(self, status: str, screen_hash: str):
        last = self._last_status
        last_hash = self._last_hash
        if last is None:
            return

        if status == "unknown":
            self._unknown_count += 1
            if self.feedback_pending and self._unknown_count >= 10:
                _log().warning("子代理状态持续无法解析 sid=%s count=%d",
                               self._session.id, self._unknown_count)
                payload = {
                    "sessionId": self._session.id,
                    "ai_status": "unknown",
                    "output": self._session.get_snapshot(keep_ansi=False),
                }
                self._publish("subagent.unknown", payload)
                self._notify("unknown", payload)
                self._unknown_count = 0
            return

        self._unknown_count = 0

        if last in self._BUSY_STATUSES and status == "idle":
            self._seen_busy = True
            self._notified.clear()
            self._complete(status)
            return

        # idle→idle 内容变化：仅当见过 busy 或已过启动宽限期后判为回合完成。
        # 宽限期兜底：快速轮（模型 10s 内完成、屏幕无 busy 帧）首轮完成时
        # 未见过 busy，但启动阶段（对话框/欢迎页切换）通常 < 宽限期，
        # 超过宽限期的 idle→idle 变化更可能是真实回合完成而非启动切换。
        if (
            last == "idle" and status == "idle"
            and last_hash is not None and screen_hash != last_hash
            and (self._seen_busy or self._past_startup_grace())
        ):
            self._notified.clear()
            self._complete(status)
            return

        if last == "idle" and status in self._BUSY_STATUSES:
            self._seen_busy = True
            self._notified.clear()
            self._publish("subagent.turn_start", {
                "sessionId": self._session.id,
                "ai_status": status,
            })
            return

        if status == "awaiting_approval":
            if "awaiting_approval" not in self._notified:
                self._notified.add("awaiting_approval")
                payload = {
                    "sessionId": self._session.id,
                    "ai_status": status,
                    "output": self._session.get_snapshot(keep_ansi=False),
                }
                self._publish("subagent.awaiting_approval", payload)
                self._notify("awaiting_approval", payload)
            return

        if status == "asking":
            if "asking" not in self._notified:
                self._notified.add("asking")
                payload = {
                    "sessionId": self._session.id,
                    "ai_status": status,
                    "output": self._session.get_snapshot(keep_ansi=False),
                }
                self._publish("subagent.asking", payload)
                self._notify("asking", payload)
            return

    def _complete(self, status: str):
        if not self.feedback_pending:
            _log().debug("turn_complete skip: no feedback_pending sid=%s", self._session.id)
            return
        self.feedback_pending = False
        payload = {
            "sessionId": self._session.id,
            "ai_status": status,
            "output": self._session.get_snapshot(keep_ansi=False),
        }
        self.last_complete = payload
        self._publish("subagent.turn_complete", payload)
        self._notify("turn_complete", payload)

    def _check_stuck(self, screen_hash: str, now: float):
        """待反馈期间屏幕静默超时 → 发布 subagent.stuck（程序未反应）并重置反馈

        触发条件：feedback_pending=True 且**见过 busy 状态**且屏幕哈希持续不变
        超过 stuck_timeout 秒。未见过 busy（模型可能尚未开始或已完成）不判
        stuck——启动阶段静默（如信任对话框）由 _seen_busy 门控排除。
        发一次即重置 feedback_pending（本轮不再重复检测），后续新反馈周期
        （feedback_pending False→True）自动重置未反应状态。
        """
        fb = self.feedback_pending
        # 反馈周期边沿：False→True 时重置未反应检测（新回合/新反馈）
        if fb and not self._prev_fb:
            self._stuck_notified = False
            self._last_change_ts = None
        self._prev_fb = fb
        if not fb:
            return
        # 从未见过 busy：可能是启动阶段静默（信任对话框/欢迎页）或模型
        # 快速完成，不判"程序未反应"，等后续 busy→idle 或内容变化判定
        if not self._seen_busy:
            return
        # 屏幕有变化 → 重置静默计时
        if self._last_hash is not None and screen_hash != self._last_hash:
            self._last_change_ts = now
            return
        # 屏幕静默：记录首次静默时刻（含首帧）
        if self._last_change_ts is None:
            self._last_change_ts = now
            return
        # 静默超时 → 程序未反应
        if now - self._last_change_ts >= self._stuck_timeout and not self._stuck_notified:
            self._stuck_notified = True
            self.feedback_pending = False
            _log().warning("子代理程序未反应 sid=%s 静默>%ss", self._session.id, self._stuck_timeout)
            payload = {
                "sessionId": self._session.id,
                "ai_status": "stuck",
                "output": self._session.get_snapshot(keep_ansi=False),
            }
            self._publish("subagent.stuck", payload)
            self._notify("stuck", payload)

    def _notify(self, kind: str, payload: dict):
        if self._notify_mgr is None:
            return
        try:
            self._notify_mgr.publish({
                "commandType": "subagent_" + kind,
                "sessionId": self._session.id,
                "ai_status": payload.get("ai_status", ""),
                "outputStream": payload.get("output", ""),
                "triggerReturnReason": kind,
                "detail": self._notify_detail(kind),
            })
        except Exception:
            _log().exception("TurnMonitor 发布通知失败: %s", self._session.id)

    def _publish(self, topic: str, payload: dict):
        if self._events is None:
            return
        try:
            self._events.publish(topic, payload, source="subagent.monitor")
        except Exception:
            _log().exception("TurnMonitor 发布事件失败: %s", topic)


def _log():
    import logging
    return logging.getLogger("pty-daemon")