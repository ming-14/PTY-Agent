"""用例层：组合 SQLite 解析与屏幕快照解析，产出完整 ParseResult。

依赖规则：用例层可依赖实体层与适配器层，不依赖框架层。
"""
from __future__ import annotations

import json
from typing import List, Optional

from .adapters import messages_db, output, screen, session_locator
from .entities import ParseResult, Session, Usage
from .infra.logging import get_logger

_log = get_logger("usecases")


class ParseSessionUseCase:
    """解析单个 Goose 会话的用例。

    工作流：
        1. 定位会话（sessionId → sessions.db sessions 表）
        2. 加载 messages 表 → List[Message]
        3. 若提供屏幕快照文本 → LiveState
        4. 返回 ParseResult
    """

    def __init__(self, data_dir: Optional[str] = None):
        """Args:
            data_dir: Goose 数据目录，None 则用默认
        """
        self._data_dir = data_dir

    def execute(
        self,
        session_id: str,
        screen_snapshot: Optional[str] = None,
    ) -> ParseResult:
        """解析指定会话。

        Args:
            session_id: 会话 ID（如 20260823_2）
            screen_snapshot: 可选的屏幕快照 VT 文本，提供则解析实时状态

        Returns:
            ParseResult
        """
        _log.info("parsing session: %s", session_id)

        row = session_locator.find_session(session_id, self._data_dir)
        conn = session_locator._connect(self._data_dir)

        try:
            messages = messages_db.load_messages(conn, session_id)
        finally:
            conn.close()

        session = self._build_session(row, messages)
        _log.info("session: %s (%s), %d messages",
                  session.name or session.id, session.cwd, len(messages))

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

    @staticmethod
    def _build_session(row: dict, messages: List) -> Session:
        """从 sessions 表行构建 Session 实体。"""
        usage = Usage(
            input_tokens=row.get("input_tokens") or 0,
            output_tokens=row.get("output_tokens") or 0,
            total_tokens=row.get("total_tokens") or 0,
            cache_read_input_tokens=row.get("cache_read_tokens") or 0,
            cache_write_input_tokens=row.get("cache_write_tokens") or 0,
        )
        accumulated = Usage(
            input_tokens=row.get("accumulated_input_tokens") or 0,
            output_tokens=row.get("accumulated_output_tokens") or 0,
            total_tokens=row.get("accumulated_total_tokens") or 0,
            cache_read_input_tokens=row.get("accumulated_cache_read_tokens") or 0,
            cache_write_input_tokens=row.get("accumulated_cache_write_tokens") or 0,
        )

        # 模型配置
        model = ""
        model_config = row.get("model_config_json")
        if model_config:
            try:
                cfg = json.loads(model_config) if isinstance(model_config, str) else model_config
                model = cfg.get("model_name", "") or ""
            except json.JSONDecodeError:
                model = ""

        # 扩展数据
        extension_data = {}
        ext_raw = row.get("extension_data")
        if ext_raw:
            try:
                extension_data = json.loads(ext_raw) if isinstance(ext_raw, str) else ext_raw
            except json.JSONDecodeError:
                extension_data = {}

        # started_at / updated_at：SQLite TIMESTAMP 格式 "2026-08-23 02:50:00"
        started_at = row.get("created_at") or ""
        updated_at = row.get("updated_at") or ""
        if started_at and "T" not in started_at:
            started_at = started_at.replace(" ", "T") + "Z"
        if updated_at and "T" not in updated_at:
            updated_at = updated_at.replace(" ", "T") + "Z"

        # last_message_at
        last_message_at = ""
        if messages:
            last_message_at = messages[-1].ts_iso

        return Session(
            id=row.get("id", ""),
            name=row.get("name", "") or "",
            cwd=row.get("working_dir", "") or "",
            started_at=started_at,
            updated_at=updated_at,
            status="idle",
            model=model,
            provider=row.get("provider_name") or "",
            goose_mode=row.get("goose_mode") or "",
            session_type=row.get("session_type") or "",
            user_set_name=bool(row.get("user_set_name", False)),
            message_count=len(messages),
            last_message_at=last_message_at,
            accumulated_cost=row.get("accumulated_cost"),
            extension_data=extension_data,
            usage=usage,
            accumulated_usage=accumulated,
        )

    def list_sessions(self) -> List[dict]:
        """列出全部会话。"""
        return session_locator.find_all_sessions(self._data_dir)

    def list_running(self) -> List[dict]:
        """列出可能的运行中会话。"""
        return session_locator.list_running_sessions(self._data_dir)

    def find_by_name(self, name: str) -> List[dict]:
        """按名称搜索会话。"""
        return session_locator.find_sessions_by_name(name, self._data_dir)