"""用例层：组合 wire.jsonl 解析与屏幕快照解析，产出完整 ParseResult。

依赖规则：用例层可依赖实体层与适配器层，不依赖框架层。
"""
from __future__ import annotations

import os
from typing import List, Optional

from .adapters import messages_wire, output, screen, session_locator
from .entities import ParseResult, Session, Usage
from .infra.logging import get_logger

_log = get_logger("usecases")


class ParseSessionUseCase:
    """解析单个 Kimi Code 会话的用例。

    工作流：
        1. 定位会话 wire.jsonl（sessionId → ~/.kimi-code/sessions/.../wire.jsonl）
        2. 加载 wire.jsonl → 会话元数据 + List[Message]
        3. 从 state.json 补充元数据（title/cwd/createdAt）
        4. 若提供屏幕快照文本 → LiveState
        5. 返回 ParseResult
    """

    def __init__(self, kimi_dir: Optional[str] = None):
        """Args:
            kimi_dir: ~/.kimi-code 路径，None 则用默认或 KIMI_CODE_HOME 环境变量
        """
        self._kimi_dir = kimi_dir

    def execute(
        self,
        session_id: str,
        screen_snapshot: Optional[str] = None,
    ) -> ParseResult:
        """解析指定会话。

        Args:
            session_id: 会话 ID（如 session_00000000-0000-0000-0000-000000000001）
            screen_snapshot: 可选的屏幕快照 VT 文本，提供则解析实时状态

        Returns:
            ParseResult
        """
        _log.info("parsing session: %s", session_id)

        path = session_locator.find_session_file(session_id, self._kimi_dir)
        _log.info("session file: %s", path)

        # 加载消息历史 + 元数据
        messages, meta = messages_wire.load_wire(path)

        # 从 state.json 补充元数据
        state = session_locator.load_state_meta(session_id, self._kimi_dir)
        if state:
            # 新版 state.json（v2，毫秒时间戳）
            started_at = state.get("createdAt", 0)
            if isinstance(started_at, int):
                meta.setdefault("started_at", str(started_at))
            else:
                # 旧版 ISO 字符串
                meta.setdefault("started_at", str(started_at) if started_at else "")
            meta.setdefault("title", state.get("title", ""))
            meta.setdefault("cwd", state.get("cwd", state.get("workDir", "")))
            meta.setdefault("is_custom_title", state.get("isCustomTitle", False))
            meta.setdefault("archived", state.get("archived", False))
            if state.get("lastTurnReason"):
                meta.setdefault("status", state["lastTurnReason"])

        session = Session(
            id=session_id,
            cwd=meta.get("cwd", ""),
            status=meta.get("status", ""),
            model=meta.get("model", ""),
            model_provider=meta.get("model_provider", ""),
            title=meta.get("title", ""),
            is_custom_title=bool(meta.get("is_custom_title", False)),
            archived=bool(meta.get("archived", False)),
            permission_mode=meta.get("permission_mode", ""),
        )
        session.started_at = meta.get("started_at", "")
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
        return session_locator.find_all_sessions(self._kimi_dir)

    @staticmethod
    def _aggregate_usage(messages) -> Usage:
        """累加所有消息的 usage 为会话级。"""
        usage = Usage()
        for m in messages:
            if m.usage:
                usage.input_tokens += m.usage.input_tokens
                usage.output_tokens += m.usage.output_tokens
                usage.cache_read_input_tokens += m.usage.cache_read_input_tokens
                usage.cache_write_input_tokens += m.usage.cache_write_input_tokens
        return usage