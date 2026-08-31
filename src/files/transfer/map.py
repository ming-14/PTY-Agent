"""传输 mtime 映射表 —— SQLite 持久化（与会话历史共用 ~/.pty-agent/history.db）

"相同文件不重传"判定依据：大小 + mtime 映射。
daemon 记住最近一次通过 upload/download 传输时：
    远端路径 → (cli_size, cli_mtime, remote_mtime)
再次传输时两端均未变（CLI 文件 mtime/size 与记录一致，且远端 mtime 与记录一致）
即判定为同一文件，跳过传输。

表结构：

    CREATE TABLE transfer_map (
        path TEXT PRIMARY KEY,
        cli_size INTEGER NOT NULL,
        cli_mtime REAL NOT NULL,
        remote_mtime REAL NOT NULL,
        updated_at REAL NOT NULL
    )

线程安全：threading.Lock + 短连接。
"""

import logging
import os
import sqlite3
import threading
import time
from typing import NamedTuple, Optional

from src.config.common import DATA_DIR

_logger = logging.getLogger("pty-daemon")

_DEFAULT_DB_PATH = os.path.join(DATA_DIR, "history.db")

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS transfer_map (
    path TEXT PRIMARY KEY,
    cli_size INTEGER NOT NULL,
    cli_mtime REAL NOT NULL,
    remote_mtime REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class TransferRecord(NamedTuple):
    """一次传输的映射记录"""
    cli_size: int
    cli_mtime: float
    remote_mtime: float


class TransferMap:
    """远端路径 → 最近一次传输记录（SQLite，每路径一行 UPSERT）"""

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
        # 共用，跨线程使用需 check_same_thread=False，串行化由 self._lock 保证）
        if self._db_path == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem_conn.executescript(_INIT_SQL)
            return self._mem_conn
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get(self, path: str) -> Optional[TransferRecord]:
        """该远端路径的最近传输记录；无记录返回 None"""
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT cli_size, cli_mtime, remote_mtime "
                    "FROM transfer_map WHERE path = ?",
                    (path,),
                ).fetchone()
        if not row:
            return None
        return TransferRecord(cli_size=row[0], cli_mtime=row[1], remote_mtime=row[2])

    def upsert(self, path: str, cli_size: int, cli_mtime: float,
               remote_mtime: float) -> None:
        """记录/更新一次传输映射（传输成功落盘后调用）"""
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO transfer_map "
                    "(path, cli_size, cli_mtime, remote_mtime, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (path, cli_size, cli_mtime, remote_mtime, time.time()),
                )
                conn.commit()

    def clear(self) -> None:
        """清空映射（测试用）"""
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM transfer_map")
                conn.commit()


_default_map = TransferMap()


def get_default_map() -> TransferMap:
    """daemon 级单例映射表（daemon 重启后从 SQLite 恢复）"""
    return _default_map