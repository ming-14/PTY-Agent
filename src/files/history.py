"""文件版本历史 —— SQLite 版本链（与会话历史共用 ~/.pty-agent/history.db）

表 files_history 结构（design §4.7）：

    CREATE TABLE files_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT NOT NULL,
        content TEXT NOT NULL,
        version TEXT NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(path, version)
    )

版本链语义（同 opencode history）：
- 每个 path 首个版本为 `0`（对应 initial），后续依次 v1/v2...（整数文本递增）
- MAX 按 CAST(version AS INTEGER) 取值，避免字符串排序 "v9" > "v10" 问题
- 线程安全：threading.Lock + 每操作短连接（仿 web/.../history_store.py 模式）
- `:memory:` 支持单测；不提供查询命令（暂不呈现 CLI），表结构为后续预留
"""

import logging
import os
import sqlite3
import threading
import time
from typing import Optional

from ..config.common import DATA_DIR

_logger = logging.getLogger("pty-daemon")

_DEFAULT_DB_PATH = os.path.join(DATA_DIR, "history.db")

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS files_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    content TEXT NOT NULL,
    version TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(path, version)
);
CREATE INDEX IF NOT EXISTS idx_files_history_path ON files_history(path);
"""


class FileHistoryStore:
    """SQLite 文件版本链存储"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._mem_conn: Optional[sqlite3.Connection] = None  # :memory: 模式持久连接
        if self._db_path != ":memory:":
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_INIT_SQL)

    def _connect(self) -> sqlite3.Connection:
        # :memory: 库随连接关闭即销毁，必须持有单条持久连接（daemon 各处理线程
        # 共用，跨线程使用需 check_same_thread=False，串行化由 self._lock 保证）；
        # 文件库沿用会话历史的短连接 + WAL 模式
        if self._db_path == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem_conn.executescript(_INIT_SQL)
            return self._mem_conn
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get_latest(self, path: str) -> Optional[dict]:
        """该 path 的最近版本（{content, version}）；无记录返回 None"""
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT content, version FROM files_history "
                    "WHERE path = ? ORDER BY CAST(version AS INTEGER) DESC LIMIT 1",
                    (path,),
                ).fetchone()
        if not row:
            return None
        return {"content": row[0], "version": row[1]}

    def create(self, path: str, content: str) -> None:
        """落 initial 版本（version=0）；已存在时幂等忽略（UNIQUE 冲突）"""
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO files_history (path, content, version, created_at) "
                    "VALUES (?, ?, '0', ?)",
                    (path, content, time.time()),
                )
                conn.commit()

    def create_version(self, path: str, content: str) -> str:
        """递增落一个新版本，返回版本号（"1"/"2"...）"""
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(CAST(version AS INTEGER)), 0) + 1 "
                    "FROM files_history WHERE path = ?",
                    (path,),
                ).fetchone()
                version = str(row[0])
                conn.execute(
                    "INSERT INTO files_history (path, content, version, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (path, content, version, time.time()),
                )
                conn.commit()
        return version