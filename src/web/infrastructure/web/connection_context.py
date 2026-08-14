"""WebSocket 连接上下文实现。

支持多会话同时订阅。
- 每个会话有独立的 output/end/event 回调
- 切换标签不再 unsubscribe 旧会话，所有订阅会话的输出持续推送
- 前端根据 ws 消息的 sessionId 字段路由到对应 xterm 实例

增加 _client_uid 字段，标识 web 客户端（localStorage 持久化）。
自适应锁以 client_uid 为持有者标识，刷新后 uid 不变，锁可恢复/继承。
"""

from typing import Any, Optional

from ...application.ports import ConnectionContext


class WebSocketConnectionContext(ConnectionContext):
    """承载单个 WebSocket 连接的会话订阅与解码器状态。

    支持多订阅：
    - _subscribed_session_ids: 所有订阅的会话 ID 集合
    - _callbacks_by_sid: 按 session_id 隔离的回调字典 {sid: {"output": cb, "end": cb, "event": cb}}

    _client_uid 标识 web 客户端，由 server.py 从 WS URL query 注入。
    """

    def __init__(self, client_uid: Optional[str] = None):
        self._subscribed_session_ids: set = set()
        self._decoders: dict[str, Any] = {}
        self._callbacks_by_sid: dict[str, dict] = {}
        # web 客户端 uid（localStorage 持久化，刷新后不变）
        self._client_uid: Optional[str] = client_uid

    @property
    def subscribed_session_ids(self) -> set:
        """返回所有订阅的会话 ID 集合的副本。"""
        return set(self._subscribed_session_ids)

    def add_subscription(self, session_id: str) -> None:
        """添加一个会话订阅。"""
        self._subscribed_session_ids.add(session_id)
        if session_id not in self._callbacks_by_sid:
            self._callbacks_by_sid[session_id] = {}

    def remove_subscription(self, session_id: str) -> None:
        """移除一个会话订阅。"""
        self._subscribed_session_ids.discard(session_id)
        self._callbacks_by_sid.pop(session_id, None)

    def get_decoder(self, session_id: str) -> Optional[Any]:
        return self._decoders.get(session_id)

    def set_decoder(self, session_id: str, decoder: Any) -> None:
        self._decoders[session_id] = decoder

    def remove_decoder(self, session_id: str) -> None:
        self._decoders.pop(session_id, None)

    def get_callbacks(self, session_id: str) -> dict:
        """按 session_id 隔离回调。"""
        return self._callbacks_by_sid.get(session_id, {})

    def set_callbacks(self, session_id: str, callbacks: dict) -> None:
        """设置指定会话的回调字典。"""
        self._callbacks_by_sid[session_id] = callbacks

    def clear_callbacks(self, session_id: Optional[str] = None) -> None:
        """清除回调。

        - 传入 session_id：只清除该会话的回调
        - 传入 None：清除所有会话的回调
        """
        if session_id:
            self._callbacks_by_sid.pop(session_id, None)
        else:
            self._callbacks_by_sid.clear()

    def clear_all_subscriptions(self) -> None:
        """清除所有订阅和回调（连接关闭场景）。"""
        self._subscribed_session_ids.clear()
        self._callbacks_by_sid.clear()

    # ── web 客户端 uid ──────────────────────────────────────────

    @property
    def client_uid(self) -> Optional[str]:
        """本连接关联的 web 客户端 uid。"""
        return self._client_uid

    def set_client_uid(self, uid: Optional[str]) -> None:
        """设置本连接的 web 客户端 uid（由 server.py 从 WS URL query 注入）。"""
        self._client_uid = uid
