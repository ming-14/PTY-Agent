"""自适应尺寸锁服务（应用层）。

自适应模式排他锁 + 接管机制。

核心规则：
- 自适应模式排他：同一会话同一时刻只有一个 client_uid 能持有自适应锁
- 持有者可自由自适应调整（ResizeObserver → fit() → resize）
- 非持有者的尺寸调整 UI 禁用，需点"接管"按钮夺取控制权
- 接管时旧持有者退出自适应变 fixed（固定当前尺寸）

锁持有者以 client_uid 标识（localStorage 持久化，刷新后不变）：
- 同一 client_uid 的多个标签页/连接共享锁（任一连接 resize 都放行）
- 刷新后 client_uid 不变，前端可从 ws_subscribed 响应识别自己持锁并恢复 UI
- _cleanup 时需检查该 client_uid 是否还有其他活跃连接订阅了该 sid，
  有则保留锁（其他连接继承），无则释放并广播

所有状态按会话 uid 索引（sid 不参与路由，避免同名 sid 复用串扰）。

线程安全：所有访问均在 asyncio event loop 中（单线程），无需加锁。
"""

from ...logging import get_logger
from typing import Optional

_logger = get_logger("pty-web")


class AdaptiveLockService:
    """会话级自适应锁管理器。

    维护 {session_uid: client_uid} 映射。
    client_uid 由前端生成并持久化在 localStorage，WS 连接 URL 携带。
    """

    def __init__(self):
        # {session_uid: client_uid} —— 按会话 uid 索引
        self._owners: dict[str, Optional[str]] = {}

    def get_owner(self, session_uid: str) -> Optional[str]:
        """查询会话的自适应锁持有者 client_uid，无持有者返回 None。"""
        return self._owners.get(session_uid)

    def is_owner(self, session_uid: str, client_uid: Optional[str]) -> bool:
        """判断指定 client_uid 是否是会话的自适应锁持有者。"""
        if not client_uid:
            return False
        owner = self._owners.get(session_uid)
        return owner is not None and owner == client_uid

    def has_owner(self, session_uid: str) -> bool:
        """会话是否已被某 client_uid 持有自适应锁。"""
        return self._owners.get(session_uid) is not None

    def acquire(self, session_uid: str, client_uid: str) -> Optional[str]:
        """夺取会话的自适应锁（排他）。

        Returns: 旧持有者的 client_uid（若存在），用于通知旧持有者降级。
        """
        old_owner = self._owners.get(session_uid)
        self._owners[session_uid] = client_uid
        _logger.info(
            "adaptive lock acquired: session_uid=%s new_owner=%s old_owner=%s",
            session_uid,
            client_uid,
            old_owner if old_owner is not None else "None",
        )
        return old_owner

    def release(self, session_uid: str, client_uid: Optional[str]) -> bool:
        """释放自适应锁（仅当 client_uid 是当前持有者时生效）。

        用于持有者主动退出自适应（切换到其他模式）或连接断开时清理。
        Returns: 是否成功释放。
        """
        if not client_uid:
            return False
        if self._owners.get(session_uid) == client_uid:
            del self._owners[session_uid]
            _logger.info(
                "adaptive lock released: session_uid=%s owner=%s", session_uid, client_uid
            )
            return True
        return False

    def clear(self, session_uid: str) -> Optional[str]:
        """强制清空会话的自适应锁（接管操作使用）。

        Returns: 被清空的旧持有者 client_uid（用于通知降级）。
        """
        old_owner = self._owners.pop(session_uid, None)
        if old_owner is not None:
            _logger.info(
                "adaptive lock cleared (takeover): session_uid=%s old_owner=%s",
                session_uid,
                old_owner,
            )
        return old_owner