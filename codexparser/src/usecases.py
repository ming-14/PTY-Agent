"""用例层：组合 rollout 解析与屏幕快照解析，产出完整 ParseResult。

依赖规则：用例层可依赖实体层与适配器层，不依赖框架层。
"""
from __future__ import annotations

import os
from typing import List, Optional

from .adapters import messages_rollout, output, screen, session_locator
from .entities import ParseResult, Session, Usage
from .infra.logging import get_logger

_log = get_logger("usecases")


class ParseSessionUseCase:
    """解析单个 codex 会话的用例。

    工作流：
        1. 定位 rollout jsonl（session_id → ~/.codex/sessions/.../rollout-*.jsonl）
        2. 加载 jsonl → 会话元数据 + List[Message]
        3. 若提供屏幕快照文本 → LiveState
        4. 返回 ParseResult
    """

    def __init__(self, codex_home: Optional[str] = None):
        """Args:
            codex_home: ~/.codex 路径，None 则用默认
        """
        self._codex_home = codex_home

    def execute(
        self,
        session_id: str,
        screen_snapshot: Optional[str] = None,
    ) -> ParseResult:
        """解析指定会话。

        Args:
            session_id: 会话 UUID（如 01a02a54-c89d-7620-88a3-304816f57e6b）
            screen_snapshot: 可选的屏幕快照 VT 文本，提供则解析实时状态

        Returns:
            ParseResult
        """
        _log.info("parsing session: %s", session_id)

        path = session_locator.find_rollout_file(session_id, self._codex_home)
        _log.info("rollout file: %s", path)

        # 加载消息历史 + 元数据
        meta, messages = messages_rollout.load_rollout(path)

        session = self._session_from_meta(meta)
        session.id = meta.get("id", session_id)

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
        return session_locator.find_all_sessions(self._codex_home)

    @staticmethod
    def _session_from_meta(meta: dict) -> Session:
        """从 rollout 元数据构建 Session 实体（供测试/复用）。"""
        return Session(
            id=meta.get("id", ""),
            started_at=meta.get("started_at", ""),
            status=meta.get("status", ""),
            model=meta.get("model", ""),
            model_provider=meta.get("model_provider", ""),
            cli_version=meta.get("cli_version", ""),
            source=meta.get("source", ""),
            cwd=meta.get("cwd", ""),
            title=meta.get("title", ""),
            approval_policy=meta.get("approval_policy", ""),
            sandbox_policy=meta.get("sandbox_policy"),
            context_window=meta.get("context_window"),
            last_error=meta.get("last_error"),
        )