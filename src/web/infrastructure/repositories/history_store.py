"""历史会话持久化实现（SQLite）。

身份模型：sessions 表以 uid 为主键（会话唯一稳定标识），
id（sid，用户自定义名）为普通列可重复 —— 同名 sid 会话先后归档
保留多条历史，不再互相覆盖。查询兼容双键：uid 优先，sid 回退取最新。

旧库迁移：旧表主键为 id（sid），启动时自动重建为 uid 主键，
旧行回填确定性 uid（legacy-<sid>-<start_time>），子表引用同步更新。
"""

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
        # sessions 表（新库直接 uid 主键；旧库被 IF NOT EXISTS 跳过，走迁移）
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS sessions (
                uid TEXT PRIMARY KEY,
                id TEXT NOT NULL,
                command TEXT NOT NULL,
                pty_type TEXT NOT NULL,
                cols INTEGER DEFAULT {DEFAULT_COLS},
                rows INTEGER DEFAULT {DEFAULT_ROWS},
                start_time REAL NOT NULL,
                end_time REAL,
                exit_code INTEGER,
                error_message TEXT,
                encoding TEXT DEFAULT 'utf-8',
                tag TEXT DEFAULT 'ended'
            )"""
        )
        # 迁移必须在子表创建前：旧库 sessions 主键还不是 uid，
        # 子表 FK REFERENCES sessions(uid) 会因父键不存在报 mismatch
        self._migrate_uid_pk(conn)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS session_output (
                session_id TEXT NOT NULL REFERENCES sessions(uid) ON DELETE CASCADE,
                stream TEXT NOT NULL,
                data_gz BLOB NOT NULL,
                original_length INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_screen (
                session_id TEXT NOT NULL REFERENCES sessions(uid) ON DELETE CASCADE,
                buffer_json_gz BLOB,
                snapshot_text TEXT
            );
            CREATE TABLE IF NOT EXISTS session_events (
                session_id TEXT NOT NULL REFERENCES sessions(uid) ON DELETE CASCADE,
                events_json TEXT NOT NULL
            );
        """)
        # 列表按 end_time 倒序，历史会话量大时走索引
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_end_time ON sessions(end_time)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_id ON sessions(id)"
        )

    def _migrate_uid_pk(self, conn: sqlite3.Connection) -> None:
        """迁移旧库：sessions 主键从 sid(id) 重建为 uid（幂等）。

        旧表 id 为主键（同名 sid 归档互相覆盖）；迁移后 uid 为主键、
        id 为可重复的展示名。旧行回填确定性 uid；子表（FK 引用 sessions）
        一并重建并从旧表拷回数据（session_id 旧值 sid → 新值 uid）。

        兼容部分迁移的中间态：sessions 已是 uid 主键但子表 FK 仍指向
        sessions(id)（早期版本迁移产物）时，同样触发子表重建。
        """
        cols = conn.execute("PRAGMA table_info(sessions)").fetchall()
        pk_cols = [c[1] for c in cols if c[5] > 0]
        sessions_ok = pk_cols == ["uid"]
        # 子表 FK 是否已指向 sessions(uid)
        children_fk_ok = True
        for t in ("session_output", "session_screen", "session_events"):
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
            ).fetchone()
            if not row:
                continue
            fks = conn.execute(f"PRAGMA foreign_key_list({t})").fetchall()
            # fk 行: (id, seq, table, from, to, on_update, on_delete, match)
            if any(fk[2] == "sessions" and fk[4] != "uid" for fk in fks):
                children_fk_ok = False
                break
        if sessions_ok and children_fk_ok:
            return  # 已迁移

        _logger.info("history migration: rebuild sessions PK id -> uid")
        # DROP TABLE sessions 在 foreign_keys=ON 下会 CASCADE 删除引用它的
        # 旧子表数据，迁移期间必须临时关闭 FK 校验（事务外生效，先 commit）
        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            self._migrate_uid_pk_locked(conn)
        finally:
            conn.execute("PRAGMA foreign_keys=ON")

    def _migrate_uid_pk_locked(self, conn: sqlite3.Connection) -> None:
        """迁移主体（须在 foreign_keys=OFF 下执行）。"""
        col_names = [c[1] for c in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        if "uid" not in col_names:
            conn.execute("ALTER TABLE sessions ADD COLUMN uid TEXT DEFAULT ''")
        # 回填旧行 uid（旧表 id 唯一，映射无歧义）
        rows = conn.execute(
            "SELECT id, start_time FROM sessions WHERE uid IS NULL OR uid = ''"
        ).fetchall()
        uid_map = {}
        for rid, st in rows:
            new_uid = f"legacy-{rid}-{int(st or 0)}"
            uid_map[rid] = new_uid
            conn.execute(
                "UPDATE sessions SET uid = ? WHERE id = ?", (new_uid, rid)
            )
        # 旧子表改名保留（数据待拷回）；新库无子表时跳过
        child_tables = ("session_output", "session_screen", "session_events")
        existing_children = [
            t for t in child_tables
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
            ).fetchone()
        ]
        for t in existing_children:
            conn.execute(f"ALTER TABLE {t} RENAME TO _legacy_{t}")
        # 重建 sessions 表（uid 主键）
        conn.executescript("""
            CREATE TABLE sessions_new (
                uid TEXT PRIMARY KEY,
                id TEXT NOT NULL,
                command TEXT NOT NULL,
                pty_type TEXT NOT NULL,
                cols INTEGER DEFAULT %d,
                rows INTEGER DEFAULT %d,
                start_time REAL NOT NULL,
                end_time REAL,
                exit_code INTEGER,
                error_message TEXT,
                encoding TEXT DEFAULT 'utf-8',
                tag TEXT DEFAULT 'ended'
            );
            INSERT INTO sessions_new (uid, id, command, pty_type, cols, rows,
                start_time, end_time, exit_code, error_message, encoding, tag)
                SELECT uid, id, command, pty_type, cols, rows,
                    start_time, end_time, exit_code, error_message, encoding, tag
                FROM sessions;
            DROP TABLE sessions;
            ALTER TABLE sessions_new RENAME TO sessions;
        """ % (DEFAULT_COLS, DEFAULT_ROWS))
        # 重建子表（FK 指向 uid），拷回数据并映射 session_id（旧值 sid → 新值 uid）
        for t in child_tables:
            if t not in existing_children:
                continue
            self._create_child_table(conn, t)
            cols_sql = {
                "session_output": "stream, data_gz, original_length",
                "session_screen": "buffer_json_gz, snapshot_text",
                "session_events": "events_json",
            }[t]
            # 拷回数据：旧子表 session_id 可能是旧 sid（按回填映射改写），
            # 也可能是已迁移的 uid（部分迁移中间态，原样拷回）
            for old_sid, new_uid in uid_map.items():
                conn.execute(
                    f"INSERT INTO {t} (session_id, {cols_sql}) "
                    f"SELECT ?, {cols_sql} FROM _legacy_{t} WHERE session_id = ?",
                    (new_uid, old_sid),
                )
            conn.execute(
                f"INSERT INTO {t} (session_id, {cols_sql}) "
                f"SELECT session_id, {cols_sql} FROM _legacy_{t} "
                f"WHERE session_id IN (SELECT uid FROM sessions) "
                f"AND session_id NOT IN (SELECT session_id FROM {t})"
            )
            conn.execute(f"DROP TABLE _legacy_{t}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_end_time ON sessions(end_time)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_id ON sessions(id)")
        conn.commit()
        _logger.info(
            "history migration done: %d rows backfilled to uid", len(uid_map)
        )

    @staticmethod
    def _create_child_table(conn: sqlite3.Connection, name: str) -> None:
        """按新 schema（FK → sessions(uid)）创建子表。"""
        if name == "session_output":
            conn.execute(
                "CREATE TABLE session_output ("
                "session_id TEXT NOT NULL REFERENCES sessions(uid) ON DELETE CASCADE,"
                "stream TEXT NOT NULL, data_gz BLOB NOT NULL, original_length INTEGER NOT NULL)"
            )
        elif name == "session_screen":
            conn.execute(
                "CREATE TABLE session_screen ("
                "session_id TEXT NOT NULL REFERENCES sessions(uid) ON DELETE CASCADE,"
                "buffer_json_gz BLOB, snapshot_text TEXT)"
            )
        elif name == "session_events":
            conn.execute(
                "CREATE TABLE session_events ("
                "session_id TEXT NOT NULL REFERENCES sessions(uid) ON DELETE CASCADE,"
                "events_json TEXT NOT NULL)"
            )

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
        uid = getattr(session, "uid", "") or ""
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
            # 按 uid 归档：同名 sid 的新会话不再覆盖旧历史
            if uid:
                conn.execute("DELETE FROM sessions WHERE uid = ?", (uid,))
            else:
                conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            conn.execute(
                "INSERT INTO sessions (uid,id,command,pty_type,cols,rows,encoding,start_time,end_time,exit_code,error_message,tag) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uid,
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
                ),
            )
            # 子表引用统一用 uid
            child_key = uid if uid else sid

            output_data = session.output_buffer.get_slice(0)
            if output_data:
                trimmed = output_data[:_MAX_OUTPUT_ARCHIVE_SIZE]
                data_gz = gzip.compress(trimmed, compresslevel=GZIP_COMPRESS_LEVEL)
                stream_name = "stdout" if getattr(session, "mode", "pty") == "subprocess" else "pty"
                conn.execute(
                    "INSERT INTO session_output (session_id,stream,data_gz,original_length) VALUES (?,?,?,?)",
                    (child_key, stream_name, data_gz, len(output_data)),
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
                            (child_key, "stderr", err_gz, len(err_data)),
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
                    (child_key, buf_gz, snapshot),
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
                        (child_key, events_json),
                    )
            except Exception:
                _logger.warning("archive events failed for %s", sid, exc_info=True)

            conn.commit()
        _logger.info("archived session %s uid=%s", sid, uid)
        return True

    def _resolve_row(self, conn: sqlite3.Connection, identifier: str):
        """按 uid 优先、sid(id) 回退（取最新）查找历史行。

        须在持锁上下文中调用；返回 row 或 None。
        """
        row = conn.execute(
            "SELECT id,command,pty_type,cols,rows,encoding,start_time,end_time,exit_code,error_message,uid "
            "FROM sessions WHERE uid = ?",
            (identifier,),
        ).fetchone()
        if row:
            return row
        return conn.execute(
            "SELECT id,command,pty_type,cols,rows,encoding,start_time,end_time,exit_code,error_message,uid "
            "FROM sessions WHERE id = ? ORDER BY end_time DESC, start_time DESC, rowid DESC LIMIT 1",
            (identifier,),
        ).fetchone()

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

    def get_session_detail(self, identifier: str) -> Optional[dict]:
        with self._lock:
            with self._connect() as conn:
                row = self._resolve_row(conn, identifier)
                if not row:
                    return None
                uid = row[10] or ""
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
                    "uid": uid,
                    "running": False,
                }
                key = uid if uid else row[0]

                screen_row = conn.execute(
                    "SELECT snapshot_text,buffer_json_gz FROM session_screen WHERE session_id = ?",
                    (key,),
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
                    (key,),
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
                    (key,),
                ).fetchone()
                if events_row and events_row[0]:
                    detail["events"] = json.loads(events_row[0])

                return detail

    def delete_session(self, identifier: str) -> bool:
        with self._lock, self._connect() as conn:
            row = self._resolve_row(conn, identifier)
            if not row:
                return False
            uid = row[10] or ""
            if uid:
                conn.execute("DELETE FROM sessions WHERE uid = ?", (uid,))
            else:
                conn.execute("DELETE FROM sessions WHERE id = ?", (row[0],))
            conn.commit()
            return True

    def get_session_tag(self, identifier: str) -> Optional[str]:
        with self._lock, self._connect() as conn:
            row = self._resolve_row(conn, identifier)
            if not row:
                return None
            uid = row[10] or ""
            key = uid if uid else row[0]
            tag_row = conn.execute(
                "SELECT tag FROM sessions WHERE uid = ?", (key,)
            ).fetchone()
            return tag_row[0] if tag_row else None

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

    def get_ended_events(self, identifier: str) -> Optional[list]:
        with self._lock, self._connect() as conn:
            row = self._resolve_row(conn, identifier)
            if not row:
                return None
            key = row[10] or row[0]
            tag_row = conn.execute(
                "SELECT tag FROM sessions WHERE uid = ?", (key,)
            ).fetchone()
            if not tag_row or tag_row[0] != "ended":
                return None
            events_row = conn.execute(
                "SELECT events_json FROM session_events WHERE session_id = ?",
                (key,),
            ).fetchone()
        if events_row and events_row[0]:
            return json.loads(events_row[0])
        return []

    def get_ended_output(self, identifier: str) -> Optional[dict]:
        with self._lock:
            with self._connect() as conn:
                row = self._resolve_row(conn, identifier)
                if not row:
                    return None
                key = row[10] or row[0]
                tag_row = conn.execute(
                    "SELECT tag FROM sessions WHERE uid = ?", (key,)
                ).fetchone()
                if not tag_row or tag_row[0] != "ended":
                    return None
                info = {
                    "command": row[1],
                    "ptyType": row[2],
                    "encoding": row[5] or "utf-8",
                    "startTime": row[6],
                    "endTime": row[7],
                    "exitCode": row[8],
                    "errorMessage": row[9],
                }
                output_rows = conn.execute(
                    "SELECT stream,data_gz FROM session_output WHERE session_id = ?",
                    (key,),
                ).fetchall()
                screen_row = conn.execute(
                    "SELECT snapshot_text FROM session_screen WHERE session_id = ?",
                    (key,),
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
