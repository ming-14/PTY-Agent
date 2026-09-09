"""会话定位适配器：在 SQLite 数据库中搜索会话。

Goose 存储布局：
- %APPDATA%\Block\goose\data\sessions\sessions.db — SQLite 数据库
- sessions 表 — 会话元数据
- 环境变量 GOOSE_PATH_ROOT 可重定向根目录

会话 ID 为 YYYYMMDD_N 序号型（如 20260823_2）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Dict, List, Optional, Tuple

from ..infra.logging import get_logger

_log = get_logger("session_locator")

# 默认数据目录（Windows）
_DEFAULT_APPDATA = os.path.join(os.environ.get("APPDATA", ""), "Block", "goose")
_SESSIONS_DB = "sessions.db"


def _data_dir() -> str:
    """获取 Goose 数据目录。

    优先用 GOOSE_PATH_ROOT 环境变量，否则用默认 %APPDATA%/Block/goose/data。
    """
    root = os.environ.get("GOOSE_PATH_ROOT")
    if root:
        return os.path.join(root, "data")
    return os.path.join(_DEFAULT_APPDATA, "data")


def _db_path(data_dir: Optional[str] = None) -> str:
    """返回 sessions.db 完整路径。"""
    base = data_dir or _data_dir()
    return os.path.join(base, "sessions", _SESSIONS_DB)


def _connect(data_dir: Optional[str] = None) -> sqlite3.Connection:
    """连接 SQLite 数据库。

    Returns:
        sqlite3.Connection（row_factory = dict）
    """
    path = _db_path(data_dir)
    _log.debug("connecting to sessions db: %s", path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def find_session(session_id: str, data_dir: Optional[str] = None) -> dict:
    """按会话 ID 查询会话元数据。

    Args:
        session_id: 会话 ID（如 20260823_2）
        data_dir: Goose 数据目录，None 则用默认

    Returns:
        sessions 表行（dict）

    Raises:
        FileNotFoundError: 数据库或会话不存在
    """
    try:
        conn = _connect(data_dir)
    except sqlite3.OperationalError as e:
        raise FileNotFoundError(f"goose sessions db not found: {e}") from e

    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(f"session not found: {session_id}")
        return dict(row)
    finally:
        conn.close()


def find_all_sessions(data_dir: Optional[str] = None) -> List[dict]:
    """列出全部会话，按 updated_at 倒序。

    Returns:
        [{session_id, name, cwd, started_at, updated_at, mtime, ...}]
    """
    try:
        conn = _connect(data_dir)
    except sqlite3.OperationalError:
        return []

    try:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            result.append(d)
        return result
    finally:
        conn.close()


def find_sessions_by_name(name: str, data_dir: Optional[str] = None) -> List[dict]:
    """按名称模糊搜索会话。

    Args:
        name: 会话名称（支持模糊匹配）
        data_dir: Goose 数据目录

    Returns:
        匹配的会话列表
    """
    try:
        conn = _connect(data_dir)
    except sqlite3.OperationalError:
        return []

    try:
        pattern = f"%{name}%"
        rows = conn.execute(
            "SELECT * FROM sessions WHERE name LIKE ? ORDER BY updated_at DESC",
            (pattern,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_running_sessions(data_dir: Optional[str] = None) -> List[dict]:
    """列出可能的运行中会话。

    Goose 没有 pid 索引文件，通过 sessions 表最近的 updated_at 排序。

    Returns:
        [{session_id, name, cwd, updated_at, ...}]
    """
    sessions = find_all_sessions(data_dir)
    # 最近 3 条可能为运行中（无可靠判断方法）
    return sessions[:3]