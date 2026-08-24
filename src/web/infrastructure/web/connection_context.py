"""WebSocket 连接上下文实现。

支持多会话同时订阅，所有内部状态按会话 uid 索引（sid 仅作展示名，
不参与路由/回调/锁的键控，避免同名 sid 复用引起的串扰）。

增加 _client_uid 字段，标识 web 客户端（localStorage 持久化）。
自适应锁以 client_uid 为持有者标识，刷新后 uid 不变，锁可恢复/继承。
"""

from typing import Any, Optional

from ...application.ports import ConnectionContext


class WebSocketConnectionContext(ConnectionContext):
    """承载单个 WebSocket 连接的会话订阅与解码器状态。

    支持多订阅：所有状态按 uid 索引。
    """

    def __init__(self, client_uid: Optional[str] = None):
        # 按 uid 索引的订阅集合、解码器、回调、持有会话
        self._subscribed_uids: set[str] = set()
        self._decoders_by_uid: dict[str, Any] = {}
        self._callbacks_by_uid: dict[str, dict] = {}
        self._held_by_uid: dict[str, Any] = {}
        # web 客户端 uid（localStorage 持久化，刷新后不变）
        self._client_uid: Optional[str] = client_uid

    @property
    def subscribed_session_ids(self) -> set:
        """返回所有订阅的会话 uid 集合的副本。"""
        return set(self._subscribed_uids)

    def add_subscription(self, session_uid: str) -> None:
        """添加一个会话订阅（按 uid）。"""
        self._subscribed_uids.add(session_uid)
        if session_uid not in self._callbacks_by_uid:
            self._callbacks_by_uid[session_uid] = {}

    def remove_subscription(self, session_uid: str) -> None:
        """移除一个会话订阅（按 uid）。"""
        self._subscribed_uids.discard(session_uid)
        self._callbacks_by_uid.pop(session_uid, None)

    def get_decoder(self, session_uid: str) -> Optional[Any]:
        return self._decoders_by_uid.get(session_uid)

    def set_decoder(self, session_uid: str, decoder: Any) -> None:
        self._decoders_by_uid[session_uid] = decoder

    def remove_decoder(self, session_uid: str) -> None:
        self._decoders_by_uid.pop(session_uid, None)

    def get_callbacks(self, session_uid: str) -> dict:
        """按 uid 隔离回调。"""
        return self._callbacks_by_uid.get(session_uid, {})

    def set_callbacks(self, session_uid: str, callbacks: dict) -> None:
        """设置指定会话的回调字典（按 uid）。"""
        self._callbacks_by_uid[session_uid] = callbacks

    def clear_callbacks(self, session_uid: Optional[str] = None) -> None:
        """清除回调。

        - 传入 uid：只清除该会话的回调
        - 传入 None：清除所有会话的回调
        """
        if session_uid:
            self._callbacks_by_uid.pop(session_uid, None)
        else:
            self._callbacks_by_uid.clear()

    def clear_all_subscriptions(self) -> None:
        """清除所有订阅和回调（连接关闭场景）。"""
        self._subscribed_uids.clear()
        self._callbacks_by_uid.clear()
        self._held_by_uid.clear()

    def add_held_session(self, session_uid: str, session: Any) -> None:
        """记录本连接对某会话的持有（按 uid）。"""
        self._held_by_uid[session_uid] = session

    def pop_held_session(self, session_uid: str) -> Optional[Any]:
        """取出并移除本连接对某会话的持有（未持有过返回 None）。"""
        return self._held_by_uid.pop(session_uid, None)

    # ── web 客户端 uid ──────────────────────────────────────────

    @property
    def client_uid(self) -> Optional[str]:
        """本连接关联的 web 客户端 uid。"""
        return self._client_uid

    def set_client_uid(self, uid: Optional[str]) -> None:
        self._client_uid = uid