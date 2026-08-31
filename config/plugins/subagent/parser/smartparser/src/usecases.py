"""用例层：组合 JSONL 解析与屏幕快照解析，产出完整 ParseResult。"""
from __future__ import annotations

from typing import List, Optional

from .adapters import messages_jsonl, output, screen, session_locator
from .entities import ParseResult, Session, Usage
from .infra.logging import get_logger

_log = get_logger("usecases")


class ParseSessionUseCase:
    """解析单个 smartagent 会话的用例。

    工作流：
        1. 定位会话 jsonl（temp/smartagent_subagent/<sid>.jsonl）
        2. 加载 jsonl → 会话元数据 + List[Message]
        3. 若提供屏幕快照文本 → LiveState
        4. 返回 ParseResult
    """

    def __init__(self, data_dir: Optional[str] = None):
        """Args:
            data_dir: 会话根目录，None 则用 temp 默认
        """
        self._data_dir = data_dir

    def execute(
        self,
        session_id: str,
        screen_snapshot: Optional[str] = None,
    ) -> ParseResult:
        """解析指定会话。"""
        _log.info("parsing session: %s", session_id)

        path = session_locator.find_session_file(session_id, self._data_dir)
        _log.info("session file: %s", path)

        # 加载消息历史 + 元数据
        meta, messages = messages_jsonl.load_jsonl_with_meta(path)

        session = Session(
            id=session_id,
            title=meta.get("title", ""),
            role=meta.get("role", ""),
            status=meta.get("status", ""),
        )
        if messages:
            session.started_at = messages[0].ts_iso or str(messages[0].ts)

        # 解析屏幕快照实时状态
        live_state = None
        if screen_snapshot:
            live_state = screen.parse_screen_snapshot(screen_snapshot)

        result = ParseResult(session=session, messages=messages, live_state=live_state)
        _log.info("parse complete: %d messages", len(messages))
        return result

    def list_sessions(self) -> List[dict]:
        """列出全部会话（供 CLI 无参调用时选择）。"""
        return session_locator.find_all_sessions(self._data_dir)


def parse_session(
    session_id: str,
    screen_snapshot: Optional[str] = None,
    data_dir: Optional[str] = None,
) -> ParseResult:
    """便捷入口：解析指定会话。"""
    usecase = ParseSessionUseCase(data_dir=data_dir)
    return usecase.execute(session_id, screen_snapshot=screen_snapshot)