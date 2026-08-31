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
    """解析单个 WorkBuddy 会话的用例。

    工作流：
        1. 定位会话 jsonl（sessionId → ~/.codebuddy/projects/*/<id>.jsonl）
        2. 加载 jsonl → 会话元数据 + List[Message]
        3. 从 workbuddy.db 补充元数据
        4. 若提供屏幕快照文本 → LiveState
        5. 返回 ParseResult
    """

    def __init__(self, workbuddy_dir: Optional[str] = None):
        """Args:
            workbuddy_dir: ~/.codebuddy 路径（cbc CLI 数据目录），None 则用默认
        """
        self._workbuddy_dir = workbuddy_dir

    def execute(
        self,
        session_id: str,
        screen_snapshot: Optional[str] = None,
    ) -> ParseResult:
        """解析指定会话。

        Args:
            session_id: 会话 ID（UUID 或 interactive-<pid>）
            screen_snapshot: 可选的屏幕快照 VT 文本，提供则解析实时状态

        Returns:
            ParseResult
        """
        _log.info("parsing session: %s", session_id)

        path = session_locator.find_session_file(session_id, self._workbuddy_dir)
        _log.info("session file: %s", path)

        # 加载消息历史 + 元数据
        messages, meta = messages_jsonl.load_jsonl(path)

        # 从 workbuddy.db 补充元数据
        db_meta = session_locator.load_db_meta(session_id, self._workbuddy_dir)
        if db_meta:
            for k, v in db_meta.items():
                meta.setdefault(k, v)

        session = Session(
            id=session_id,
            cwd=meta.get("cwd", ""),
            status=meta.get("status", ""),
            model=meta.get("model", ""),
            model_provider=meta.get("model_provider", ""),
            cli_version=meta.get("cli_version", ""),
            mode=meta.get("mode", ""),
            permission_mode=meta.get("permission_mode", ""),
            source_mode=meta.get("source_mode", ""),
            title=meta.get("title", ""),
        )
        session.started_at = meta.get("started_at", "")
        if messages:
            session.started_at = session.started_at or str(messages[0].ts)
        session.usage = self._aggregate_usage(messages)

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
        return session_locator.find_all_sessions(self._workbuddy_dir)

    def list_running(self) -> List[dict]:
        """列出运行中会话索引。"""
        return session_locator.list_running_sessions(self._workbuddy_dir)

    @staticmethod
    def _aggregate_usage(messages) -> Usage:
        """累加所有消息的 usage 为会话级。"""
        usage = Usage()
        for m in messages:
            if m.usage:
                usage.input_tokens += m.usage.input_tokens
                usage.output_tokens += m.usage.output_tokens
                usage.total_tokens += m.usage.total_tokens
                usage.cached_tokens += m.usage.cached_tokens
        return usage
