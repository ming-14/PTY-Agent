"""用例层：组合 DB 解析与屏幕快照解析，产出完整 ParseResult。

依赖规则：用例层可依赖实体层与适配器层，不依赖框架层。
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import List, Optional

from .adapters import messages_db, output, screen, session_locator
from .entities import ParseResult, Session, Usage
from .infra.logging import get_logger

_log = get_logger("usecases")


class ParseSessionUseCase:
    """解析单个 opencode 会话的用例。

    工作流：
        1. 定位 opencode.db（data_dir → opencode.db）
        2. 从 session 表加载会话元数据
        3. 从 message/part 表加载消息历史
        4. 若提供屏幕快照文本 → LiveState
        5. 返回 ParseResult
    """

    def __init__(self, data_dir: Optional[str] = None):
        """Args:
            data_dir: opencode 数据目录（含 opencode.db），None 则用默认
        """
        self._data_dir = data_dir

    def execute(
        self,
        session_id: str,
        screen_snapshot: Optional[str] = None,
    ) -> ParseResult:
        """解析指定会话。

        Args:
            session_id: 会话 ID（如 ses_xxx）
            screen_snapshot: 可选的屏幕快照 VT 文本，提供则解析实时状态

        Returns:
            ParseResult

        Raises:
            FileNotFoundError: opencode.db 不存在
            KeyError: 会话不存在
        """
        _log.info("parsing session: %s", session_id)

        con = session_locator.open_db(self._data_dir)
        try:
            session = self._load_session(con, session_id)
            messages, usage = messages_db.load_session_messages(con, session_id)
        finally:
            con.close()
        session.usage = usage

        # 解析屏幕快照实时状态
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

    def _load_session(self, con: sqlite3.Connection, session_id: str) -> Session:
        """从 session 表加载 Session 实体。"""
        row = con.execute(
            "SELECT * FROM session WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"session not found: {session_id}")

        d = dict(row)
        model_info: dict = {}
        model = d.get("model") or ""
        if isinstance(model, str) and model.startswith("{"):
            try:
                model_info = json.loads(model)
            except json.JSONDecodeError:
                model_info = {}

        started_at = d.get("time_created") or 0
        # 首条消息时间作为 started_at（会话创建时间可能早于首条消息）
        first_msg = con.execute(
            "SELECT MIN(time_created) FROM message WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if first_msg and first_msg[0]:
            started_at = first_msg[0]

        usage = Usage(
            input_tokens=d.get("tokens_input") or 0,
            output_tokens=d.get("tokens_output") or 0,
            reasoning_tokens=d.get("tokens_reasoning") or 0,
            cache_read_input_tokens=d.get("tokens_cache_read") or 0,
            cache_write_input_tokens=d.get("tokens_cache_write") or 0,
            total_cost=d.get("cost") or 0.0,
        )

        return Session(
            id=session_id,
            slug=d.get("slug") or "",
            cwd=d.get("directory") or "",
            path=d.get("path") or "",
            title=d.get("title") or "",
            status="running" if d.get("time_archived") is None else "archived",
            agent=d.get("agent") or "",
            model=model_info.get("id") or model,
            model_provider=model_info.get("providerID") or "",
            variant=model_info.get("variant") or "",
            version=d.get("version") or "",
            cost=d.get("cost") or 0.0,
            started_at=str(started_at) if started_at else "",
            usage=usage,
            parent_id=d.get("parent_id"),
            permission=d.get("permission"),
        )


def parse_session(
    session_id: str,
    screen_snapshot: Optional[str] = None,
    data_dir: Optional[str] = None,
) -> ParseResult:
    """便捷入口：解析指定会话。

    Args:
        session_id: 会话 ID
        screen_snapshot: 可选的屏幕快照 VT 文本
        data_dir: opencode 数据目录，None 则用默认

    Returns:
        ParseResult
    """
    usecase = ParseSessionUseCase(data_dir=data_dir)
    return usecase.execute(session_id, screen_snapshot=screen_snapshot)