"""用例层：组合 transcript 解析与屏幕快照解析，产出完整 ParseResult。

依赖规则：用例层可依赖实体层与适配器层，不依赖框架层。
"""
from __future__ import annotations

from typing import List, Optional

from .adapters import messages_transcript, screen, session_locator
from .entities import ParseResult, Session, Usage
from .infra.logging import get_logger

_log = get_logger("usecases")


class ParseSessionUseCase:
    """解析单个 Devin 会话的用例。

    工作流：
        1. 定位 transcript JSON（session_id → %APPDATA%\\devin\\cli\\transcripts\\<id>.json）
        2. 加载 JSON → 会话元数据 + List[Message]
        3. 从 SQLite 索引补充会话字段（cwd / agent_mode / title 等，尽力而为）
        4. 若提供屏幕快照文本 → LiveState
        5. 返回 ParseResult
    """

    def __init__(self, devin_home: Optional[str] = None):
        """Args:
            devin_home: %APPDATA%\\devin\\cli 路径，None 则用默认
        """
        self._devin_home = devin_home

    def execute(
        self,
        session_id: str,
        screen_snapshot: Optional[str] = None,
    ) -> ParseResult:
        """解析指定会话。

        Args:
            session_id: 会话 ID（如 blend-pencil）
            screen_snapshot: 可选的屏幕快照 VT 文本，提供则解析实时状态

        Returns:
            ParseResult
        """
        _log.info("parsing session: %s", session_id)

        path = session_locator.find_transcript_file(session_id, self._devin_home)
        _log.info("transcript file: %s", path)

        # 加载消息历史 + 元数据
        meta, messages = messages_transcript.load_transcript(path)

        session = self._session_from_meta(meta, session_id)
        self._enrich_from_sqlite(session)

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
        self._finalize(result)
        _log.info("parse complete: %d messages, live_state=%s",
                  len(messages), live_state is not None)
        return result

    def list_sessions(self) -> List[dict]:
        """列出全部会话（供 CLI 无参调用时选择）。"""
        return session_locator.find_all_sessions(self._devin_home)

    def list_running(self) -> List[dict]:
        """列出运行中会话。"""
        return session_locator.list_running_sessions(self._devin_home)

    @staticmethod
    def _session_from_meta(meta: dict, session_id: str) -> Session:
        """从 transcript 元数据构建 Session 实体。"""
        session = Session(
            id=meta.get("id") or session_id,
            model=meta.get("model", ""),
            cli_version=meta.get("cli_version", ""),
            source=meta.get("source", ""),
            title=meta.get("title", ""),
            usage=Usage(
                total_prompt_tokens=meta.get("total_prompt_tokens", 0),
                total_completion_tokens=meta.get("total_completion_tokens", 0),
                total_cached_tokens=meta.get("total_cached_tokens", 0),
                total_steps=meta.get("total_steps", 0),
            ),
        )
        return session

    def _enrich_from_sqlite(self, session: Session) -> None:
        """从 SQLite 索引补充会话字段（尽力而为，失败不阻断）。"""
        try:
            sessions = session_locator.find_all_sessions(self._devin_home)
        except Exception as e:
            _log.warning("failed to list sessions from sqlite: %s", e)
            return
        for s in sessions:
            if s["session_id"] != session.id:
                continue
            session.cwd = s.get("cwd", "")
            session.model = s.get("model", "") or session.model
            session.agent_mode = s.get("agent_mode", "")
            session.backend_type = s.get("backend_type", "")
            session.title = s.get("title", "") or session.title
            break

        # status：从 session_locks 判定是否运行中
        try:
            running = session_locator.list_running_sessions(self._devin_home)
        except Exception as e:
            _log.warning("failed to list running sessions: %s", e)
            running = []
        session.status = "running" if any(
            r["session_id"] == session.id and r.get("pid") for r in running
        ) else "idle"
        return

    def _finalize(self, result: ParseResult) -> ParseResult:
        """组装收尾：started_at 取首条消息时间戳。"""
        if result.messages:
            result.session.started_at = result.messages[0].ts_iso
        return result


def parse_session(
    session_id: str,
    screen_snapshot: Optional[str] = None,
    devin_home: Optional[str] = None,
) -> ParseResult:
    """便捷入口：解析指定会话。

    Args:
        session_id: 会话 ID
        screen_snapshot: 可选的屏幕快照 VT 文本
        devin_home: %APPDATA%\\devin\\cli 路径，None 则用默认

    Returns:
        ParseResult
    """
    usecase = ParseSessionUseCase(devin_home=devin_home)
    return usecase.execute(session_id, screen_snapshot=screen_snapshot)