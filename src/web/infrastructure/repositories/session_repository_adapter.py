"""SessionManager 的 SessionRepository 适配器。"""

from typing import Any, Callable, Optional

from ...application.ports import SessionRepository
from ...domain.entities import ActiveSession, SessionEndedInfo
from ....session.session import Session


class SessionRepositoryAdapter(SessionRepository):
    """将项目已有的 SessionManager 适配为应用层 SessionRepository 端口。"""

    def __init__(self, manager: Any):
        self._manager = manager

    def list_sessions(self) -> list[ActiveSession]:
        sessions = self._manager.list_sessions()
        return [
            ActiveSession(
                id=s["id"],
                uid=s.get("uid", ""),
                command=s["command"],
                running=s["running"],
                start_time=s["startTime"],
            )
            for s in sessions
        ]

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._manager.get_session(session_id)

    def create_session(
        self,
        session_id: str,
        command,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
    ) -> Any:
        return self._manager.create_session(
            session_id, command, cwd=cwd, env=env, cols=cols, rows=rows
        )

    def remove_session(self, session_id: str) -> Optional[SessionEndedInfo]:
        session = self._manager.get_session(session_id)
        self._manager.remove_session(session_id)
        if session:
            return SessionEndedInfo(
                session_id=session_id,
                exit_code=session.exit_code,
                error_message=session.error_message,
            )
        return None

    def set_on_session_created(self, callback: Callable[[str], None]) -> None:
        self._manager.set_on_session_created(callback)

    def set_on_session_removed(
        self, callback: Callable[[str, Optional[int], Optional[str]], None]
    ) -> None:
        self._manager.set_on_session_removed(callback)
