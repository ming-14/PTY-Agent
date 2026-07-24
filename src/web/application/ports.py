"""应用层端口（抽象接口）。

定义了应用层与外部基础设施交互所需的能力。内层（应用层、领域层）
通过这些抽象与外层（基础设施层）解耦。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from ...session.session import Session

from ..domain.entities import (
    ActiveSession,
    HistoryDetail,
    HistorySession,
    OutputChunk,
    SessionDetail,
    SessionEndedInfo,
    SessionEvent,
    SystemStats,
)


class SessionRepository(ABC):
    """活跃会话仓储抽象。"""

    @abstractmethod
    def list_sessions(self) -> list[ActiveSession]:
        """列出所有活跃会话。"""

    @abstractmethod
    def get_session(self, session_id: str) -> Optional["Session"]:
        """获取会话底层对象；不存在返回 None。"""

    @abstractmethod
    def create_session(
        self,
        session_id: str,
        command,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
    ) -> Any:
        """创建并启动会话。"""

    @abstractmethod
    def remove_session(self, session_id: str) -> Optional[SessionEndedInfo]:
        """移除会话并返回结束信息。"""

    @abstractmethod
    def set_on_session_created(self, callback: Callable[[str], None]) -> None:
        """设置会话创建回调。"""

    @abstractmethod
    def set_on_session_removed(
        self, callback: Callable[[str, Optional[int], Optional[str]], None]
    ) -> None:
        """设置会话移除回调。"""


class HistoryRepository(ABC):
    """历史记录仓储抽象。"""

    @abstractmethod
    def list_sessions(self) -> list[HistorySession]:
        """列出历史会话。"""

    @abstractmethod
    def get_session_detail(self, session_id: str) -> Optional[HistoryDetail]:
        """获取历史会话详情。"""

    @abstractmethod
    def delete_session(self, session_id: str) -> bool:
        """删除历史会话。"""


class OutboundMessageChannel(ABC):
    """出站消息通道抽象（连接级）。"""

    @abstractmethod
    async def send(self, message: dict) -> None:
        """发送 JSON 消息。"""

    @abstractmethod
    async def close(self, code: int = 1000) -> None:
        """关闭通道。"""

    @property
    @abstractmethod
    def closed(self) -> bool:
        """是否已关闭。"""


class ConnectionContext(ABC):
    """WebSocket 连接上下文抽象。

    承载连接级状态，如当前订阅的会话集合、消息队列等。

    C2 改造（模拟 WT）：支持多会话同时订阅。
    - 每个会话有独立的 output/end/event 回调
    - 切换标签不再 unsubscribe 旧会话，所有订阅会话的输出持续推送
    - 前端根据 ws 消息的 sessionId 字段路由到对应 xterm 实例

    v3 改造：增加 client_uid 字段，标识 web 客户端（持久化在 localStorage）。
    自适应锁以 client_uid 为持有者标识，刷新页面后 uid 不变，锁可恢复/继承。
    """

    @property
    @abstractmethod
    def subscribed_session_id(self) -> Optional[str]:
        """当前活动订阅的会话 ID（兼容旧代码，返回最近订阅的会话）。

        C2 改造后保留此属性仅为兼容，实际多订阅状态用 subscribed_session_ids。
        """

    @property
    @abstractmethod
    def subscribed_session_ids(self) -> set:
        """当前所有订阅的会话 ID 集合（C2 多订阅）。"""

    @abstractmethod
    def set_subscribed_session_id(self, session_id: Optional[str]) -> None:
        """设置当前活动订阅的会话 ID（兼容旧代码）。

        C2 改造后：传入非 None 时自动 add 到 subscribed_session_ids，
        传入 None 时清空所有订阅（用于连接关闭）。
        """

    @abstractmethod
    def add_subscription(self, session_id: str) -> None:
        """添加一个会话订阅（C2 多订阅）。"""

    @abstractmethod
    def remove_subscription(self, session_id: str) -> None:
        """移除一个会话订阅（C2 多订阅）。"""

    @abstractmethod
    def get_decoder(self, session_id: str) -> Optional[Any]:
        """获取会话的增量解码器。"""

    @abstractmethod
    def set_decoder(self, session_id: str, decoder: Any) -> None:
        """设置会话的增量解码器。"""

    @abstractmethod
    def remove_decoder(self, session_id: str) -> None:
        """移除会话的增量解码器。"""

    @abstractmethod
    def get_callbacks(self, session_id: str) -> dict:
        """获取指定会话的回调字典（output/end/event）。

        C2 改造：按 session_id 隔离回调，支持多订阅。
        """

    @abstractmethod
    def set_callbacks(self, session_id: str, callbacks: dict) -> None:
        """设置指定会话的回调字典（C2 新增）。"""

    @abstractmethod
    def clear_callbacks(self, session_id: Optional[str] = None) -> None:
        """清除回调。

        C2 改造：
        - 传入 session_id 时只清除该会话的回调
        - 传入 None 时清除所有会话的回调（用于连接关闭）
        """

    @abstractmethod
    def clear_all_subscriptions(self) -> None:
        """清除所有订阅和回调（用于连接关闭，C2 新增）。"""

    @property
    @abstractmethod
    def client_uid(self) -> Optional[str]:
        """本连接关联的 web 客户端 uid（v3 新增）。

        由前端生成并持久化在 localStorage，WS 连接 URL 携带。
        自适应锁以 client_uid 为持有者标识，刷新后 uid 不变，锁可恢复/继承。
        """

    @abstractmethod
    def set_client_uid(self, uid: Optional[str]) -> None:
        """设置本连接的 web 客户端 uid（v3 新增）。

        在 WS 连接建立时由 server.py 从 URL query 读取并注入。
        """


class SystemStatsProvider(ABC):
    """系统资源统计提供者抽象。"""

    @abstractmethod
    async def get_stats(self) -> SystemStats:
        """获取 CPU / 内存使用率。"""


class ShellProvider(ABC):
    """可用 Shell 列表提供者抽象。"""

    @abstractmethod
    def list_shells(self) -> dict:
        """返回 shell 名称到路径的映射。"""


class EventPublisher(ABC):
    """会话事件发布者抽象（广播）。"""

    @abstractmethod
    def publish_session_created(self, session_id: str, uid: str = "") -> None:
        """广播会话创建事件（携带 uid 以便前端即时更新会话 uid）。"""

    @abstractmethod
    def publish_session_removed(
        self, session_id: str, exit_code: Optional[int], error_message: Optional[str]
    ) -> None:
        """广播会话移除事件。"""

    @abstractmethod
    def publish_session_resized(
        self,
        session_id: str,
        cols: int,
        rows: int,
        snapshot: str,
        scrollback: str,
        exclude_conn_id: Optional[Any] = None,
    ) -> None:
        """广播会话尺寸变更事件（定向：仅发给订阅该会话的客户端）。

        问题1（尺寸变更通知）：当任意来源（网页 resize 请求 / 守护进程命令行）
        触发会话尺寸变更后，必须立刻通知所有订阅该会话的客户端调整终端显示。

        - 仅发给 context.subscribed_session_ids 包含 session_id 的连接
        - exclude_conn_id 指定发起方连接 ID（id(transport)），避免发起方重复处理
          （发起方已通过 resize_complete 消息完成本地调整）
        - snapshot / scrollback 为后端 reflow 后的完整内容，客户端据此重建 buffer
        """

    @abstractmethod
    def publish_size_mode_changed(
        self,
        session_id: str,
        adaptive_owner_active: bool,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
        mode: Optional[str] = None,
        adaptive_owner_uid: Optional[str] = None,
        exclude_conn_id: Optional[Any] = None,
    ) -> None:
        """广播尺寸模式变更事件（定向：仅发给订阅该会话的客户端）。

        问题2（自适应排他锁）：当自适应锁状态变更（接管/释放/降级）或
        某客户端显式设定新尺寸模式时，通知所有订阅客户端更新 UI。

        v3 改造：增加 adaptive_owner_uid，前端据此判断"自己是否持锁"并恢复 UI。
        - adaptive_owner_active: 是否有 client_uid 持有自适应锁
        - adaptive_owner_uid: 当前持有者的 client_uid（无持有者时为 None）
        - mode/cols/rows: 当 SetSizeMode 设定新模式时的尺寸信息（供其他客户端跟随）
        - exclude_conn_id: 排除发起方（发起方已自行处理本地模式切换）
        """


class ThreadExecutor(ABC):
    """线程执行器抽象，用于在线程池中执行同步 I/O。"""

    @abstractmethod
    async def run(self, fn: Callable, *args, **kwargs) -> Any:
        """在线程池中执行可调用对象。"""


