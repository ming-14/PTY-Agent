"""WebSocket 连接上下文实现。

C2 改造（模拟 WT）：支持多会话同时订阅。
- 每个会话有独立的 output/end/event 回调
- 切换标签不再 unsubscribe 旧会话，所有订阅会话的输出持续推送
- 前端根据 ws 消息的 sessionId 字段路由到对应 xterm 实例

v3 改造：增加 _client_uid 字段，标识 web 客户端（localStorage 持久化）。
自适应锁以 client_uid 为持有者标识，刷新后 uid 不变，锁可恢复/继承。
"""

from typing import Any, Optional

from ...application.ports import ConnectionContext


class WebSocketConnectionContext(ConnectionContext):
    """承载单个 WebSocket 连接的会话订阅与解码器状态。

    C2 改造后支持多订阅：
    - _subscribed_session_ids: 所有订阅的会话 ID 集合
    - _active_session_id: 最近订阅的活动会话（兼容旧代码）
    - _callbacks_by_sid: 按 session_id 隔离的回调字典 {sid: {"output": cb, "end": cb, "event": cb}}

    v3 改造：_client_uid 标识 web 客户端，由 server.py 从 WS URL query 注入。
    """

    def __init__(self, client_uid: Optional[str] = None):
        self._subscribed_session_ids: set = set()
        self._active_session_id: Optional[str] = None
        self._decoders: dict[str, Any] = {}
        self._callbacks_by_sid: dict[str, dict] = {}
        # v3: web 客户端 uid（localStorage 持久化，刷新后不变）
        self._client_uid: Optional[str] = client_uid

    @property
    def subscribed_session_id(self) -> Optional[str]:
        """兼容旧代码：返回最近订阅的活动会话 ID。"""
        return self._active_session_id

    @property
    def subscribed_session_ids(self) -> set:
        """C2 多订阅：返回所有订阅的会话 ID 集合的副本。"""
        return set(self._subscribed_session_ids)

    def set_subscribed_session_id(self, session_id: Optional[str]) -> None:
        """兼容旧代码：设置活动会话 ID。

        C2 改造后：
        - 传入非 None：同时 add 到订阅集合
        - 传入 None：清空所有订阅（连接关闭场景）
        """
        if session_id is None:
            self.clear_all_subscriptions()
        else:
            self.add_subscription(session_id)

    def add_subscription(self, session_id: str) -> None:
        """添加一个会话订阅（C2 多订阅）。"""
        self._subscribed_session_ids.add(session_id)
        self._active_session_id = session_id
        if session_id not in self._callbacks_by_sid:
            self._callbacks_by_sid[session_id] = {}

    def remove_subscription(self, session_id: str) -> None:
        """移除一个会话订阅（C2 多订阅）。"""
        self._subscribed_session_ids.discard(session_id)
        self._callbacks_by_sid.pop(session_id, None)
        # 如果移除的是活动会话，重新选一个作为活动会话
        if self._active_session_id == session_id:
            self._active_session_id = (
                next(iter(self._subscribed_session_ids), None)
                if self._subscribed_session_ids else None
            )

    def get_decoder(self, session_id: str) -> Optional[Any]:
        return self._decoders.get(session_id)

    def set_decoder(self, session_id: str, decoder: Any) -> None:
        self._decoders[session_id] = decoder

    def remove_decoder(self, session_id: str) -> None:
        self._decoders.pop(session_id, None)

    def get_callbacks(self, session_id: str) -> dict:
        """C2 改造：按 session_id 隔离回调。

        旧代码可能不传 session_id（无参数调用），此处做兼容：
        - 不传或传 None：返回活动会话的回调（兼容旧代码）
        - 传 session_id：返回该会话的回调
        """
        # 兼容旧代码无参数调用（已通过抽象基类签名约束为必传）
        sid = session_id if session_id else self._active_session_id
        if not sid:
            return {}
        return self._callbacks_by_sid.get(sid, {})

    def set_callbacks(self, session_id: str, callbacks: dict) -> None:
        """C2 新增：设置指定会话的回调字典。"""
        self._callbacks_by_sid[session_id] = callbacks

    def clear_callbacks(self, session_id: Optional[str] = None) -> None:
        """C2 改造：清除回调。

        - 传入 session_id：只清除该会话的回调
        - 传入 None：清除所有会话的回调
        """
        if session_id:
            self._callbacks_by_sid.pop(session_id, None)
        else:
            self._callbacks_by_sid.clear()

    def clear_all_subscriptions(self) -> None:
        """C2 新增：清除所有订阅和回调（连接关闭场景）。"""
        self._subscribed_session_ids.clear()
        self._callbacks_by_sid.clear()
        self._active_session_id = None

    # ── v3: web 客户端 uid ──────────────────────────────────────────

    @property
    def client_uid(self) -> Optional[str]:
        """本连接关联的 web 客户端 uid。"""
        return self._client_uid

    def set_client_uid(self, uid: Optional[str]) -> None:
        """设置本连接的 web 客户端 uid（由 server.py 从 WS URL query 注入）。"""
        self._client_uid = uid
