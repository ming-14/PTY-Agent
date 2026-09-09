"""用例层：组合 JSONL 解析与屏幕快照解析，产出完整 ParseResult。

依赖规则：用例层可依赖实体层与适配器层，不依赖框架层。
"""
from __future__ import annotations

import os
from typing import List, Optional

from .adapters import messages_jsonl, output, screen, session_locator
from .entities import ParseResult, Session, Usage
from .infra.logging import get_logger

_log = get_logger("usecases")


class ParseSessionUseCase:
    """解析单个 AtomCode 会话的用例。

    工作流：
        1. 定位会话 jsonl + meta（sessionId → sessions/<cwd-hash>/）
        2. 加载 jsonl → List[Message]；加载 meta → Session 元数据
        3. 若提供屏幕快照文本 → LiveState
        4. 返回 ParseResult
    """

    def __init__(self, data_dir: Optional[str] = None):
        """Args:
            data_dir: $ATOMCODE_HOME 路径，None 则用默认
        """
        self._data_dir = data_dir

    def execute(
        self,
        session_id: str,
        screen_snapshot: Optional[str] = None,
    ) -> ParseResult:
        """解析指定会话。

        Args:
            session_id: 会话 UUID（如 86f15020-b057-4954-8c5f-93e9e2074c38）
            screen_snapshot: 可选的屏幕快照 VT 文本，提供则解析实时状态

        Returns:
            ParseResult

        Raises:
            FileNotFoundError: 会话不存在
        """
        _log.info("parsing session: %s", session_id)

        jsonl_path = session_locator.find_session_file(session_id, self._data_dir, ext="jsonl")
        meta_path = session_locator.find_session_file(session_id, self._data_dir, ext="meta")
        _log.info("session jsonl: %s", jsonl_path)

        # 加载消息历史 + 元数据
        messages = messages_jsonl.load_jsonl(jsonl_path)
        meta = messages_jsonl.load_meta_dict(meta_path)
        session = messages_jsonl.parse_session_meta(meta, messages)

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
        """列出可能的运行中会话。"""
        return session_locator.list_running_sessions(self._data_dir)