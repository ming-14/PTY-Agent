"""历史会话持久化实现（SQLite）。"""

import gzip
import json
import os
import sqlite3
import threading
from typing import Optional

from ....config.common import DATA_DIR, DEFAULT_COLS, DEFAULT_ROWS, GZIP_COMPRESS_LEVEL
from ....logging import get_logger

_logger = get_logger("pty-web")

_DEFAULT_DB_PATH = os.path.join(DATA_DIR, "history.db")
_MAX_OUTPUT_ARCHIVE_SIZE = 10 * 1024 * 1024


class HistoryStore:
    """基于 SQLite 的历史会话存储。"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        if self._db_path != ":memory:":
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._lock = threading.Lock()
        # 长连接：所有操作经 self._lock 串行化（check_same_thread=False），
        # 避免每次操作新建连接 + 重复执行 PRAGMA 的开销
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        conn = self._connect()
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                command TEXT NOT NULL,
                pty_type TEXT NOT NULL,
                cols INTEGER DEFAULT {DEFAULT_COLS},
                rows INTEGER DEFAULT {DEFAULT_ROWS},
                start_time REAL NOT NULL,
                end_time REAL,
                exit_code INTEGER,
                error_message TEXT
            );
            CREATE TABLE IF NOT EXISTS session_output (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                stream TEXT NOT NULL,
                data_gz BLOB NOT NULL,
                original_length INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_screen (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                buffer_json_gz BLOB,
                snapshot_text TEXT
            );
            CREATE TABLE IF NOT EXISTS session_events (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                events_json TEXT NOT NULL
            );
        """)
        # 列表按 end_time 倒序，历史会话量大时走索引
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_end_time ON sessions(end_time)"
        )
        try:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN encoding TEXT DEFAULT 'utf-8'"
            )
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN tag TEXT DEFAULT 'ended'")
        except sqlite3.OperationalError:
            pass
        # 添加 uid 列，持久化会话 uid 供历史会话恢复 frameRatio
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN uid TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass

    def _connect(self) -> sqlite3.Connection:
        """获取长连接（须在持锁上下文中调用；连接失效时重建）"""
        if self._conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
        return self._conn

    def archive_session(self, session, tag: str = "ended") -> bool:
        try:
            with self._lock:
                return self._archive_session_locked(session, tag)
        except Exception:
            _logger.exception("archive_session failed: %s", session.id)
            return False

    def _archive_session_locked(self, session, tag: str = "ended") -> bool:
        sid = session.id
        command = (
            session.command
            if isinstance(session.command, str)
            else " ".join(session.command)
        )
        pty_type = (
            "subprocess"
            if getattr(session, "mode", "pty") == "subprocess"
            else session.pty_type
        )
        cols = session.cols
        rows = session.rows
        encoding = session.encoding or "utf-8"
        start_time = session.start_time
        end_time = _now()
        exit_code = session.exit_code
        error_message = session.error_message

        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            conn.execute(
                "INSERT INTO sessions (id,command,pty_type,cols,rows,encoding,start_time,end_time,exit_code,error_message,tag,uid) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sid,
                    command,
                    pty_type,
                    cols,
                    rows,
                    encoding,
                    start_time,
                    end_time,
                    exit_code,
                    error_message,
                    tag,
                    getattr(session, "uid", "") or "",
                ),
            )

            output_data = session.output_buffer.get_slice(0)
            if output_data:
                trimmed = output_data[:_MAX_OUTPUT_ARCHIVE_SIZE]
                data_gz = gzip.compress(trimmed, compresslevel=GZIP_COMPRESS_LEVEL)
                stream_name = "stdout" if getattr(session, "mode", "pty") == "subprocess" else "pty"
                conn.execute(
                    "INSERT INTO session_output (session_id,stream,data_gz,original_length) VALUES (?,?,?,?)",
                    (sid, stream_name, data_gz, len(output_data)),
                )

            # 子进程模式：独立归档 stderr
            if getattr(session, "mode", "pty") == "subprocess":
                try:
                    err_data = session._err_buf.get_slice(0) if session._err_buf else b""
                    if err_data:
                        err_trimmed = err_data[:_MAX_OUTPUT_ARCHIVE_SIZE]
                        err_gz = gzip.compress(err_trimmed, compresslevel=GZIP_COMPRESS_LEVEL)
                        conn.execute(
                            "INSERT INTO session_output (session_id,stream,data_gz,original_length) VALUES (?,?,?,?)",
                            (sid, "stderr", err_gz, len(err_data)),
                        )
                except Exception:
                    _logger.debug("archive stderr failed for %s", sid, exc_info=True)

            try:
                screen_buf = session.export_screen_buffer()
                snapshot = session.get_snapshot(keep_ansi=True)
                buf_gz = None
                if screen_buf:
                    raw = json.dumps(
                        screen_buf, ensure_ascii=True, separators=(",", ":")
                    ).encode("utf-8")
                    buf_gz = gzip.compress(raw, compresslevel=GZIP_COMPRESS_LEVEL)
                conn.execute(
                    "INSERT INTO session_screen (session_id,buffer_json_gz,snapshot_text) VALUES (?,?,?)",
                    (sid, buf_gz, snapshot),
                )
            except Exception:
                _logger.warning("archive screen failed for %s", sid, exc_info=True)

            try:
                events = session.get_all_events()
                if events:
                    events_json = json.dumps(
                        events, ensure_ascii=True, separators=(",", ":")
                    )
                    conn.execute(
                        "INSERT INTO session_events (session_id,events_json) VALUES (?,?)",
                        (sid, events_json),
                    )
            except Exception:
                _logger.warning("archive events failed for %s", sid, exc_info=True)

            conn.commit()
        _logger.info("archived session %s", sid)
        return True

    def list_sessions(self) -> list:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id,command,pty_type,encoding,start_time,end_time,exit_code,error_message,uid "
                    "FROM sessions ORDER BY end_time DESC"
                ).fetchall()
        result = []
        for r in rows:
            result.append(
                {
                    "id": r[0],
                    "command": r[1],
                    "ptyType": r[2],
                    "encoding": r[3],
                    "startTime": r[4],
                    "endTime": r[5],
                    "exitCode": r[6],
                    "errorMessage": r[7],
                    "uid": r[8] or "",
                    "running": False,
                }
            )
        return result

    def get_session_detail(self, session_id: str) -> Optional[dict]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT id,command,pty_type,cols,rows,encoding,start_time,end_time,exit_code,error_message,uid "
                    "FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                if not row:
                    return None
                detail = {
                    "id": row[0],
                    "command": row[1],
                    "ptyType": row[2],
                    "cols": row[3],
                    "rows": row[4],
                    "encoding": row[5] or "utf-8",
                    "startTime": row[6],
                    "endTime": row[7],
                    "exitCode": row[8],
                    "errorMessage": row[9],
                    "uid": row[10] or "",
                    "running": False,
                }

                screen_row = conn.execute(
                    "SELECT snapshot_text,buffer_json_gz FROM session_screen WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if screen_row:
                    detail["snapshot"] = screen_row[0] or ""
                    if screen_row[1]:
                        detail["screenBufferZ"] = base64_encode(screen_row[1])
                        detail["screenBufferMeta"] = {
                            "compressed": True,
                            "sparse": True,
                        }

                output_rows = conn.execute(
                    "SELECT stream,data_gz,original_length FROM session_output WHERE session_id = ?",
                    (session_id,),
                ).fetchall()
                replay_parts = []
                for orow in output_rows:
                    stream, data_gz, original_length = orow
                    detail["outputGz"] = base64_encode(data_gz)
                    detail["outputGzOriginalLen"] = original_length
                    if data_gz:
                        try:
                            data = gzip.decompress(data_gz)
                        except Exception:
                            data = b""
                        if data:
                            text = data.decode(detail["encoding"], errors="replace")
                            replay_parts.append(text)

                replay = "".join(replay_parts)
                if not replay and screen_row and screen_row[0]:
                    replay = screen_row[0] or ""
                detail["replay"] = replay

                events_row = conn.execute(
                    "SELECT events_json FROM session_events WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if events_row and events_row[0]:
                    detail["events"] = json.loads(events_row[0])

                return detail

    def delete_session(self, session_id: str) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            return conn.total_changes > 0

    def get_session_tag(self, session_id: str) -> Optional[str]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT tag FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return row[0] if row else None

    def list_ended_sessions(self) -> list:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id,command,pty_type,encoding,start_time,end_time,exit_code,error_message "
                    "FROM sessions WHERE tag = 'ended' ORDER BY end_time DESC"
                ).fetchall()
        result = []
        for r in rows:
            result.append(
                {
                    "id": r[0],
                    "command": r[1],
                    "ptyType": r[2],
                    "encoding": r[3],
                    "startTime": r[4],
                    "endTime": r[5],
                    "exitCode": r[6],
                    "errorMessage": r[7],
                    "running": False,
                }
            )
        return result

    def mark_all_ended_as_history(self) -> int:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE sessions SET tag = 'history' WHERE tag = 'ended'"
                )
                conn.commit()
                return cur.rowcount

    def get_ended_events(self, session_id: str) -> Optional[list]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT tag FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not row or row[0] != "ended":
                return None
            events_row = conn.execute(
                "SELECT events_json FROM session_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if events_row and events_row[0]:
            return json.loads(events_row[0])
        return []

    def get_ended_output(self, session_id: str) -> Optional[dict]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT tag,command,pty_type,encoding,start_time,end_time,exit_code,error_message "
                    "FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                if not row or row[0] != "ended":
                    return None
                info = {
                    "command": row[1],
                    "ptyType": row[2],
                    "encoding": row[3] or "utf-8",
                    "startTime": row[4],
                    "endTime": row[5],
                    "exitCode": row[6],
                    "errorMessage": row[7],
                }
                output_rows = conn.execute(
                    "SELECT stream,data_gz FROM session_output WHERE session_id = ?",
                    (session_id,),
                ).fetchall()
                screen_row = conn.execute(
                    "SELECT snapshot_text FROM session_screen WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
        enc = info["encoding"]
        stdout_parts = []
        stderr_parts = []
        for orow in output_rows:
            stream, data_gz = orow
            try:
                data = gzip.decompress(data_gz) if data_gz else b""
            except Exception:
                data = b""
            text = data.decode(enc, errors="replace")
            if stream == "stderr":
                stderr_parts.append(text)
            else:
                stdout_parts.append(text)
        if stdout_parts:
            info["output"] = "".join(stdout_parts)
        if stderr_parts:
            info["stderrOutput"] = "".join(stderr_parts)
        if screen_row:
            info["snapshot"] = screen_row[0] or ""
        return info


def _now() -> float:
    import time

    return time.time()


def base64_encode(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")
