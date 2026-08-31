"""会话定位适配器：在 opencode.db 中搜索会话。

opencode 存储布局：
- ~/.local/share/opencode/opencode.db — SQLite 主存储（session/message/part/event 表）
- ~/.local/share/opencode/log/opencode.log — 运行日志

会话 ID 形如 ses_xxx（如 ses_ffecfe685ffeGCr3ZSBObXSlhu）。
运行中会话判定：part 表存在 status=running 的工具调用（进行中）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Dict, List, Optional, Tuple

from ..infra.logging import get_logger

_log = get_logger("session_locator")

DEFAULT_DATA_DIR = os.path.join(
    os.path.expanduser("~"), ".local", "share", "opencode"
)
_DB_NAME = "opencode.db"

# 会话 ID 前缀：ses_
_SESSION_ID_PREFIX = "ses_"


def _db_path(data_dir: Optional[str]) -> str:
    base = data_dir or DEFAULT_DATA_DIR
    return os.path.join(base, _DB_NAME)


def open_db(data_dir: Optional[str] = None) -> sqlite3.Connection:
    """打开 opencode.db（只读）。"""
    path = _db_path(data_dir)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"opencode.db not found: {path}")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def find_session(session_id: str, data_dir: Optional[str] = None) -> Optional[dict]:
    """按 session_id 查询会话元数据。

    Args:
        session_id: 会话 ID（如 ses_xxx）
        data_dir: opencode 数据目录，None 则用默认

    Returns:
        会话 dict 或 None
    """
    con = open_db(data_dir)
    try:
        row = con.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def find_session_by_title(title: str, data_dir: Optional[str] = None) -> str:
    """定位会话 ID：uid 为 title（oneshot --title）或 session_id（interactive 发现）。

    Args:
        title: 会话标题（spawn 时经 --title 指定，唯一）或直接是 ses_xxx ID
        data_dir: opencode 数据目录，None 则用默认

    Returns:
        会话 ID（如 ses_xxx）

    Raises:
        FileNotFoundError: 未找到
    """
    if not title:
        raise FileNotFoundError("empty session title")
    con = open_db(data_dir)
    try:
        # uid 本身是 session_id（interactive 发现后直接传入）
        if title.startswith(_SESSION_ID_PREFIX):
            row = con.execute(
                "SELECT id FROM session WHERE id = ?", (title,)
            ).fetchone()
            if row:
                return row[0]
        # 按 title 定位（oneshot --title 唯一标记）
        row = con.execute(
            "SELECT id FROM session WHERE title = ? ORDER BY time_created DESC LIMIT 1",
            (title,),
        ).fetchone()
        if row:
            return row[0]
    except (sqlite3.Error, OSError) as e:
        _log.warning("failed to query session by title: %s", e)
        raise FileNotFoundError(f"session by title not found: {title}") from e
    raise FileNotFoundError(f"session by title not found: {title}")


def find_all_sessions(data_dir: Optional[str] = None) -> List[dict]:
    """列出全部会话。

    Returns:
        [{session_id, title, cwd, model, agent, cost, time_updated}]，按最后修改时间倒序
    """
    con = open_db(data_dir)
    try:
        rows = con.execute(
            "SELECT id, title, directory, path, model, agent, cost, "
            "tokens_input, tokens_output, time_created, time_updated "
            "FROM session ORDER BY time_updated DESC"
        ).fetchall()
        sessions: List[dict] = []
        for r in rows:
            model = ""
            if r["model"]:
                try:
                    model = json.loads(r["model"]).get("id", "") if r["model"].startswith("{") else r["model"]
                except json.JSONDecodeError:
                    model = r["model"]
            sessions.append({
                "session_id": r["id"],
                "title": r["title"] or "",
                "cwd": r["directory"] or "",
                "path": r["path"] or "",
                "model": model,
                "agent": r["agent"] or "",
                "cost": r["cost"] or 0.0,
                "tokens_input": r["tokens_input"] or 0,
                "tokens_output": r["tokens_output"] or 0,
                "time_created": r["time_created"] or 0,
                "time_updated": r["time_updated"] or 0,
            })
        return sessions
    finally:
        con.close()


def list_running_sessions(data_dir: Optional[str] = None) -> List[dict]:
    """列出运行中会话。

    判定依据：part 表存在 status=running 的工具调用（进行中），
    即该会话有工具正在执行。

    Returns:
        [{session_id, tool, call_id, started_at}]
    """
    con = open_db(data_dir)
    try:
        rows = con.execute(
            "SELECT session_id, data FROM part "
            "WHERE data LIKE '%\"status\":\"running\"%'"
        ).fetchall()
        seen: Dict[str, dict] = {}
        for r in rows:
            try:
                pd = json.loads(r["data"])
            except json.JSONDecodeError:
                continue
            if pd.get("type") != "tool":
                continue
            state = pd.get("state") or {}
            if state.get("status") != "running":
                continue
            sid = r["session_id"]
            seen[sid] = {
                "session_id": sid,
                "tool": pd.get("tool", ""),
                "call_id": pd.get("callID", ""),
                "started_at": (state.get("time") or {}).get("start", 0)
                if isinstance(state.get("time"), dict) else 0,
            }
        return list(seen.values())
    finally:
        con.close()