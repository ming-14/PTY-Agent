"""用例层：组合 JSONL 解析与屏幕快照解析，产出完整 ParseResult。

依赖规则：用例层可依赖实体层与适配器层，不依赖框架层。
"""
from __future__ import annotations

import os
from typing import List, Optional

from .adapters import messages_jsonl, screen, session_locator
from .entities import ParseResult, Session, Usage
from .infra.logging import get_logger

_log = get_logger("usecases")


class ParseSessionUseCase:
    """解析单个 Gemini CLI 会话的用例。

    工作流：
        1. 定位会话 jsonl（sessionId → ~/.gemini/tmp/*/chats/*.jsonl）
        2. 加载 jsonl → 会话元数据 + List[Message]
        3. 若提供屏幕快照文本 → LiveState
        4. 返回 ParseResult
    """

    def __init__(self, gemini_dir: Optional[str] = None):
        """Args:
            gemini_dir: ~/.gemini 路径，None 则用默认
        """
        self._gemini_dir = gemini_dir

    def execute(
        self,
        session_id: str,
        screen_snapshot: Optional[str] = None,
    ) -> ParseResult:
        """解析指定会话。

        Args:
            session_id: 会话 UUID（如 00000000-0000-0000-0000-000000000001）
            screen_snapshot: 可选的屏幕快照 VT 文本，提供则解析实时状态

        Returns:
            ParseResult
        """
        _log.info("parsing session: %s", session_id)

        path = session_locator.find_session_file(session_id, self._gemini_dir)
        _log.info("session file: %s", path)

        # 加载消息历史 + 元数据
        meta, messages = messages_jsonl.load_jsonl(path)

        # 获取项目名和 cwd
        project = path.split(os.sep)[-3] if os.sep in path else ""
        cwd = ""
        if project:
            cwd = session_locator._load_projects(self._gemini_dir).get(project, "")

        session = Session(
            id=session_id,
            cwd=cwd,
            project=project,
            model=meta.get("model", ""),
            status=meta.get("status", ""),
            summary=meta.get("summary", ""),
            version=meta.get("kind", ""),
        )
        # started_at：首行 startTime
        session.started_at = meta.get("startTime", "")
        # title：首条非系统用户消息
        for m in messages:
            if m.role == "user" and m.items:
                text = m.items[0].text or ""
                if text:
                    session.title = text[:80]
                    break
        # model：取首条 message 的 model
        for m in messages:
            if m.model:
                session.model = m.model
                break
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
        return session_locator.find_all_sessions(self._gemini_dir)

    def list_running(self) -> List[dict]:
        """列出运行中会话。"""
        return session_locator.list_running_sessions(self._gemini_dir)

    @staticmethod
    def _aggregate_usage(messages) -> Usage:
        """累加所有消息的 usage 为会话级。"""
        usage = Usage()
        for m in messages:
            if m.usage:
                usage.input_tokens += m.usage.input_tokens
                usage.output_tokens += m.usage.output_tokens
                usage.cached_tokens += m.usage.cached_tokens
                usage.thoughts_tokens += m.usage.thoughts_tokens
                usage.tool_tokens += m.usage.tool_tokens
                usage.total_tokens += m.usage.total_tokens
                usage.total_cost += m.usage.total_cost
        return usage