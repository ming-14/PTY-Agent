"""会话管理器 — SessionManager

管理所有 PTY 会话的创建、获取、列出、移除和批量停止。
"""

import logging
import threading
from typing import Optional, Callable

from .session import Session
from ..config.daemon import MAX_SESSIONS

_logger = logging.getLogger("pty-session")


class SessionManager:
    """会话管理器

    负责会话 CRUD 操作和生命周期管理，线程安全。
    """

    def __init__(self, history_store=None):
        self._sessions: dict = {}
        self._lock = threading.Lock()
        self._history_store = history_store
        self._on_session_created: Optional[Callable[[str], None]] = None
        self._on_session_removed: Optional[Callable[[str, Optional[int], Optional[str]], None]] = None

    def set_on_session_created(self, cb: Optional[Callable[[str], None]]):
        self._on_session_created = cb

    def set_on_session_removed(self, cb: Optional[Callable[[str, Optional[int], Optional[str]], None]]):
        self._on_session_removed = cb

    def create_session(
        self,
        session_id: str,
        command,
        encoding: Optional[str] = None,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        snapshot_mode: bool = False,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
    ) -> Session:
        if not session_id or not isinstance(session_id, str):
            raise ValueError("会话 ID 必须为非空字符串")
        _logger.debug("create_session: sid=%r cmd=%r cwd=%r cols=%s rows=%s", session_id, command, cwd, cols, rows)
        with self._lock:
            if session_id in self._sessions:
                _logger.warning("create_session: session already exists sid=%r", session_id)
                raise KeyError(f"会话 '{session_id}' 已存在")
            if len(self._sessions) >= MAX_SESSIONS:
                _logger.warning("create_session: max sessions reached (%d)", MAX_SESSIONS)
                raise ValueError(f"会话数已达上限 ({MAX_SESSIONS})，请先 kill 不需要的会话")
            s = Session(session_id, command, encoding=encoding,
                        cwd=cwd, env=env, snapshot_mode=snapshot_mode,
                        cols=cols, rows=rows)
            self._sessions[session_id] = s
        s._publisher.add_on_end_callback(lambda sess: self._on_session_ended(sess))
        _logger.info("create_session: starting sid=%r cmd=%r", session_id, command)
        try:
            s.start()
        except Exception:
            _logger.warning("create_session: start failed sid=%r, removing tombstone", session_id)
            with self._lock:
                self._sessions.pop(session_id, None)
            try:
                s.stop()
            except Exception:
                pass
            raise
        _logger.info("create_session: started sid=%r pty=%s", session_id, s.pty_type)
        if self._on_session_created:
            try:
                self._on_session_created(session_id)
            except Exception:
                _logger.exception("on_session_created callback error")
        return s

    def get_session(self, session_id: str) -> Optional[Session]:
        """获取指定会话

        Args:
            session_id: 会话标识符。

        Returns:
            Session 实例，不存在时返回 None。
        """
        with self._lock:
            return self._sessions.get(session_id)

    def list_sessions(self) -> list:
        """列出所有会话（含已结束但未移除的）

        Returns:
            dict 列表，每项包含 id/command/running/startTime 字段。
        """
        with self._lock:
            return [
                {
                    "id": s.id,
                    "uid": s.uid,
                    "command": (
                        s.command
                        if isinstance(s.command, str)
                        else " ".join(s.command)
                    ),
                    "running": s.running,
                    "startTime": s.start_time,
                }
                for s in self._sessions.values()
            ]

    def _on_session_ended(self, session):
        """会话自然结束时的回调：移除活跃列表、释放资源、归档、广播事件

        注意：此回调在读者线程（notify_end 同步广播）内执行，
        session.stop 已支持由当前线程调用（见 SessionThreads.stop）。
        """
        with self._lock:
            if session.id not in self._sessions:
                return  # 已被 remove_session 处理
            self._sessions.pop(session.id, None)

        # 释放会话资源（含沙箱进程），避免自然结束的会话泄漏
        try:
            session.stop()
            _logger.info("会话 '%s' 自然结束，资源已释放", session.id)
        except Exception as e:
            _logger.warning("会话 '%s' 自然结束释放资源时异常: %s", session.id, e)

        if self._history_store:
            try:
                self._history_store.archive_session(session, tag="ended")
            except Exception as e:
                _logger.warning("归档会话 '%s' 时异常: %s", session.id, e)

        if self._on_session_removed:
            try:
                self._on_session_removed(session.id, session.exit_code, session.error_message)
            except Exception:
                _logger.exception("on_session_removed callback error")

    def remove_session(self, session_id: str):
        """移除并停止指定会话（移除前持久化到历史）

        Args:
            session_id: 会话标识符。
        """
        import time as _time
        _t0 = _time.monotonic()
        _logger.info("remove_session: sid=%r", session_id)
        with self._lock:
            s = self._sessions.pop(session_id, None)
        if s:
            if self._history_store:
                try:
                    self._history_store.archive_session(s, tag="history")
                    _logger.debug("remove_session: archived sid=%r took %.3fs",
                                  session_id, _time.monotonic() - _t0)
                except Exception as e:
                    _logger.warning("持久化会话 '%s' 时异常: %s", session_id, e)
            _t1 = _time.monotonic()
            try:
                s.stop()
                _logger.info("remove_session: stopped sid=%r stop took %.3fs",
                             session_id, _time.monotonic() - _t1)
            except Exception as e:
                _logger.warning("移除会话 '%s' 时异常: %s", session_id, e)
            _t2 = _time.monotonic()
            if self._on_session_removed:
                try:
                    self._on_session_removed(session_id, s.exit_code, s.error_message)
                except Exception:
                    _logger.exception("on_session_removed callback error")
            _logger.info("remove_session: done sid=%r total %.3fs on_removed %.3fs",
                         session_id, _time.monotonic() - _t0, _time.monotonic() - _t2)

    def stop_all(self):
        """停止所有会话"""
        with self._lock:
            ids = list(self._sessions.keys())
        _logger.info("stop_all: stopping %d sessions", len(ids))
        for sid in ids:
            self.remove_session(sid)
