"""HistoryStore 的 HistoryRepository 适配器。"""

from typing import Optional

from ...application.ports import HistoryRepository
from ...domain.entities import HistoryDetail, HistorySession
from .history_store import HistoryStore


class HistoryRepositoryAdapter(HistoryRepository):
    """将项目已有的 SQLite HistoryStore 适配为应用层 HistoryRepository 端口。"""

    def __init__(self, store: HistoryStore):
        self._store = store

    def list_sessions(self) -> list[HistorySession]:
        rows = self._store.list_sessions()
        return [
            HistorySession(
                id=r["id"],
                command=r["command"],
                pty_type=r["ptyType"],
                encoding=r["encoding"],
                start_time=r["startTime"],
                end_time=r["endTime"],
                exit_code=r["exitCode"],
                error_message=r["errorMessage"],
                uid=r.get("uid", ""),
            )
            for r in rows
        ]

    def get_session_detail(self, session_id: str) -> Optional[HistoryDetail]:
        raw = self._store.get_session_detail(session_id)
        if not raw:
            return None
        return HistoryDetail(
            id=raw["id"],
            command=raw["command"],
            pty_type=raw["ptyType"],
            cols=raw["cols"],
            rows=raw["rows"],
            encoding=raw["encoding"],
            start_time=raw["startTime"],
            end_time=raw["endTime"],
            exit_code=raw["exitCode"],
            error_message=raw["errorMessage"],
            uid=raw.get("uid", ""),
            replay=raw.get("replay", ""),
            snapshot=raw.get("snapshot", ""),
            screen_buffer_z=raw.get("screenBufferZ"),
            screen_buffer_meta=raw.get("screenBufferMeta"),
            output_gz=raw.get("outputGz"),
            output_gz_original_len=raw.get("outputGzOriginalLen"),
            events=raw.get("events"),
        )

    def delete_session(self, session_id: str) -> bool:
        return self._store.delete_session(session_id)
