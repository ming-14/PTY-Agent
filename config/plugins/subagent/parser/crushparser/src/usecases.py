"""用例层：组合 DB 解析与屏幕快照解析，产出完整 ParseResult。

依赖规则：用例层可依赖实体层与适配器层，不依赖框架层。
"""
from __future__ import annotations

import os
import sqlite3
from typing import List, Optional

from .adapters import messages_db, output, screen, session_locator
from .entities import ParseResult, Session
from .infra.logging import get_logger

_log = get_logger("usecases")


class ParseSessionUseCase:
    """解析单个 Crush 会话的用例。

    工作流：
        1. 定位 crush.db（projects.json → data_dir → crush.db）
        2. 从 sessions 表加载会话元数据
        3. 从 messages 表加载消息历史
        4. 若提供屏幕快照文本 → LiveState
        5. 返回 ParseResult
    """

    def __init__(self, data_dir: Optional[str] = None):
        """Args:
            data_dir: 显式 crush 数据目录（含 crush.db），None 则遍历 projects.json
        """
        self._data_dir = data_dir

    def execute(
        self,
        session_id: str,
        screen_snapshot: Optional[str] = None,
    ) -> ParseResult:
        """解析指定会话。

        Args:
            session_id: 会话 ID（UUID / XXH3 hash / hash 前缀）
            screen_snapshot: 可选的屏幕快照 VT 文本，提供则解析实时状态

        Returns:
            ParseResult

        Raises:
            FileNotFoundError: crush.db 不存在
            KeyError: 会话不存在
        """
        _log.info("parsing session: %s", session_id)

        # 1. 定位会话
        srow = session_locator.find_session(session_id, self._data_dir)
        if srow is None:
            raise KeyError(f"session not found: {session_id}")

        db_path = os.path.join(srow["data_dir"], "crush.db")
        if not os.path.isfile(db_path):
            raise FileNotFoundError(f"crush.db not found: {db_path}")

        # 2. 连接数据库
        con = session_locator.open_db(db_path)
        try:
            session = messages_db.load_session_entity(con, srow["id"])
            session.data_dir = srow["data_dir"]
            messages, usage = messages_db.load_session_messages(con, srow["id"])
        finally:
            con.close()

        # 聚合 usage 到会话级（sessions 表已有统计数据，但消息级 usage 为 0）
        # session 实体已从 sessions 表加载 prompt/completion_tokens 和 cost

        # 3. 解析屏幕快照实时状态
        live_state = None
        if screen_snapshot:
            _log.info("parsing screen snapshot for live state")
            live_state = screen.parse_screen_snapshot(screen_snapshot)

        result = ParseResult(
            session=session,
            messages=messages,
            live_state=live_state,
        )
        _log.info("parse complete: %d messages, live_state=%s",
                  len(messages), live_state is not None)
        return result

    def list_sessions(self) -> List[dict]:
        """列出全部会话（供 CLI 无参调用时选择）。"""
        return session_locator.find_all_sessions(self._data_dir)

    def list_running(self) -> List[dict]:
        """列出运行中会话。"""
        return session_locator.list_running_sessions(self._data_dir)


def parse_session(
    session_id: str,
    screen_snapshot: Optional[str] = None,
    data_dir: Optional[str] = None,
) -> ParseResult:
    """便捷入口：解析指定会话。

    Args:
        session_id: 会话 ID
        screen_snapshot: 可选的屏幕快照 VT 文本
        data_dir: crush 数据目录，None 则遍历 projects.json

    Returns:
        ParseResult
    """
    usecase = ParseSessionUseCase(data_dir=data_dir)
    return usecase.execute(session_id, screen_snapshot=screen_snapshot)