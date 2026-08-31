"""基于连接管理器的事件发布者实现。"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from ...application.ports import EventPublisher
from ....logging import get_logger

_logger = get_logger("pty-web")


class EventPublisherImpl(EventPublisher):
    """向所有活跃 WebSocket 连接广播会话生命周期事件。"""

    def __init__(
        self,
        connections: dict[Any, dict],
        loop_getter: Callable[[], Optional[asyncio.AbstractEventLoop]],
    ):
        self._connections = connections
        self._get_loop = loop_getter

    def publish_session_created(self, session_id: str, uid: str = "") -> None:
        self._broadcast("session_created", session_id, session_uid=uid, uid=uid)

    def publish_session_removed(
        self,
        session_id: str,
        uid: str = "",
        exit_code: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        self._broadcast(
            "session_removed",
            session_id,
            session_uid=uid,
            uid=uid,
            exit_code=exit_code,
            error_message=error_message,
        )

    def publish_session_resized(
        self,
        session_uid: str,
        session_id: str,
        cols: int,
        rows: int,
        snapshot: str,
        scrollback: str,
        exclude_conn_id: Optional[Any] = None,
    ) -> None:
        """定向广播尺寸变更：仅发给订阅该会话的客户端，排除发起方。

        尺寸变更通知。发起方已通过 resize_complete 完成本地调整，
        其他订阅客户端通过 session_resized 同步调整终端尺寸与 buffer。
        订阅关系按 uid 索引（sid 复用不串扰）。
        """
        self._broadcast(
            "session_resized",
            session_id,
            session_uid=session_uid,
            cols=cols,
            rows=rows,
            snapshot=snapshot,
            scrollback=scrollback,
            filter_by_uid=True,
            exclude_conn_id=exclude_conn_id,
        )

    def publish_size_mode_changed(
        self,
        session_uid: str,
        session_id: str,
        adaptive_owner_active: bool,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
        mode: Optional[str] = None,
        adaptive_owner_uid: Optional[str] = None,
        exclude_conn_id: Optional[Any] = None,
    ) -> None:
        """定向广播尺寸模式变更：仅发给订阅该会话的客户端，排除发起方。

        通知所有客户端当前自适应锁状态 + 模式变更，
        前端据此禁用/启用尺寸调整 UI，被降级端切换到 fixed 模式。

        携带 adaptive_owner_uid，前端据此判断"自己是否持锁"并恢复 UI。
        订阅关系按 uid 索引（sid 复用不串扰）。
        """
        self._broadcast(
            "size_mode_changed",
            session_id,
            session_uid=session_uid,
            adaptive_owner_active=adaptive_owner_active,
            adaptive_owner_uid=adaptive_owner_uid,
            cols=cols,
            rows=rows,
            mode=mode,
            filter_by_uid=True,
            exclude_conn_id=exclude_conn_id,
        )

    def _broadcast(
        self,
        event_type: str,
        session_id: str,
        session_uid: str = "",
        exit_code: Optional[int] = None,
        error_message: Optional[str] = None,
        uid: str = "",
        # 定向广播参数（尺寸变更通知 / 模式变更）
        cols: Optional[int] = None,
        rows: Optional[int] = None,
        snapshot: str = "",
        scrollback: str = "",
        mode: Optional[str] = None,
        adaptive_owner_active: Optional[bool] = None,
        adaptive_owner_uid: Optional[str] = None,
        filter_by_uid: bool = False,
        exclude_conn_id: Optional[Any] = None,
    ) -> None:
        loop = self._get_loop()
        if not loop:
            _logger.warning("broadcast %s: no event loop available", event_type)
            return

        payload = {"type": event_type, "sessionId": session_id}
        if session_uid:
            payload["sessionUid"] = session_uid
        if exit_code is not None:
            payload["exitCode"] = exit_code
        if error_message is not None:
            payload["errorMessage"] = error_message
        if uid:
            payload["uid"] = uid
        if cols is not None:
            payload["cols"] = cols
        if rows is not None:
            payload["rows"] = rows
        if snapshot:
            payload["snapshot"] = snapshot
        if scrollback:
            payload["scrollback"] = scrollback
        if mode is not None:
            payload["mode"] = mode
        if adaptive_owner_active is not None:
            payload["adaptiveOwnerActive"] = adaptive_owner_active
        # 携带持锁者 client_uid，前端据此判断"自己是否持锁"并恢复 UI
        if adaptive_owner_uid is not None:
            payload["adaptiveOwnerUid"] = adaptive_owner_uid

        def _emit():
            # 定向广播：仅发给订阅该 session uid 的连接；排除发起方
            if filter_by_uid:
                targets = []
                for conn_id, conn in self._connections.items():
                    if exclude_conn_id is not None and conn_id == exclude_conn_id:
                        continue
                    context = conn.get("context")
                    if not context:
                        continue
                    try:
                        if session_uid and session_uid in context.subscribed_session_ids:
                            targets.append(conn)
                    except Exception as e:
                        _logger.debug(
                            "broadcast %s: filter uid check error: %s", event_type, e
                        )
                _logger.info(
                    "broadcast %s: uid=%r filtered to %d subscribers (excluded initiator=%s)",
                    event_type,
                    session_uid,
                    len(targets),
                    exclude_conn_id is not None,
                )
                conns = targets
            else:
                conns = list(self._connections.values())
                _logger.info(
                    "broadcast %s: sid=%r to %d connections",
                    event_type,
                    session_id,
                    len(conns),
                )
            for conn in conns:
                try:
                    conn["queue"].put_nowait(payload)
                except asyncio.QueueFull:
                    _logger.warning(
                        "broadcast %s: queue full, dropping (uid=%r)",
                        event_type,
                        session_uid,
                    )
                except Exception as e:
                    _logger.debug("broadcast %s: queue put error: %s", event_type, e)

        try:
            loop.call_soon_threadsafe(_emit)
        except Exception as e:
            _logger.exception("broadcast call_soon_threadsafe error: %s", e)
