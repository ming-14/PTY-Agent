"""会话定位适配器：在 %APPDATA%\\devin\\cli 下搜索会话文件。

Devin 存储布局：
- %APPDATA%\\devin\\cli\\transcripts\\<session-name>.json — 消息历史
- %APPDATA%\\devin\\cli\\sessions.db — SQLite 会话索引
- %APPDATA%\\devin\\cli\\session_locks\\<name>.lock — 运行中会话锁（PID）
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple

from ..infra.logging import get_logger

_log = get_logger("session_locator")

DEFAULT_DEVIN_HOME = os.path.join(os.environ.get("APPDATA", ""), "devin", "cli")
_TRANSCRIPTS_DIR = "transcripts"
_SESSIONS_DB = "sessions.db"
_SESSION_LOCKS_DIR = "session_locks"

# 会话 ID 为形容词-名词组合（如 blend-pencil）
_SESSION_ID_RE = re.compile(r"^[a-z]+-[a-z]+$")


def _process_alive(pid: int) -> bool:
    """检查进程是否存活（跨平台）。

    - POSIX: os.kill(pid, 0) 探测
    - Windows: OpenProcess 查询（os.kill 的 sig=0 不受支持）
    """
    try:
        if sys.platform == "win32":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError, SystemError, ValueError):
        return False


def _devin_dirs(devin_home: Optional[str]) -> Tuple[str, str, str]:
    """返回 (transcripts_dir, sessions_db, session_locks_dir)。"""
    base = devin_home or DEFAULT_DEVIN_HOME
    return (
        os.path.join(base, _TRANSCRIPTS_DIR),
        os.path.join(base, _SESSIONS_DB),
        os.path.join(base, _SESSION_LOCKS_DIR),
    )


def find_transcript_file(session_id: str, devin_home: Optional[str] = None) -> str:
    """在 transcripts 目录下搜索指定 session_id 的 JSON 文件。

    Args:
        session_id: 会话 ID（如 blend-pencil）
        devin_home: %APPDATA%\\devin\\cli 路径，None 则用默认

    Returns:
        JSON 文件路径

    Raises:
        FileNotFoundError: 未找到
    """
    transcripts_dir, _, _ = _devin_dirs(devin_home)
    path = os.path.join(transcripts_dir, f"{session_id}.json")
    if os.path.isfile(path):
        return path
    raise FileNotFoundError(f"transcript not found: {session_id}")


def find_all_sessions(devin_home: Optional[str] = None) -> List[dict]:
    """列出所有会话。

    优先从 SQLite 索引读取，降级为扫描 transcripts 目录。

    Returns:
        [{session_id, path, cwd, model, title, created_at, last_activity_at, agent_mode, backend_type, status}]
    """
    transcripts_dir, sessions_db, _ = _devin_dirs(devin_home)
    sessions: List[dict] = []

    # 优先从 SQLite 读取
    if os.path.isfile(sessions_db):
        try:
            sessions = _load_from_sqlite(sessions_db, transcripts_dir)
        except Exception as e:
            _log.warning("failed to read sessions.db: %s", e)
            sessions = []

    # 降级：扫描 transcripts 目录
    if not sessions and os.path.isdir(transcripts_dir):
        sessions = _scan_transcripts_dir(transcripts_dir)

    return sessions


def _load_from_sqlite(db_path: str, transcripts_dir: str) -> List[dict]:
    """从 SQLite 加载会话列表。"""
    sessions: List[dict] = []
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        for row in cur.execute(
            "SELECT id, working_directory, model, title, agent_mode, backend_type,"
            " created_at, last_activity_at, hidden"
            " FROM sessions ORDER BY last_activity_at DESC"
        ):
            session_id = row[0]
            path = os.path.join(transcripts_dir, f"{session_id}.json")
            sessions.append({
                "session_id": session_id,
                "path": path if os.path.isfile(path) else "",
                "cwd": row[1] or "",
                "model": row[2] or "",
                "title": row[3] or "",
                "agent_mode": row[4] or "",
                "backend_type": row[5] or "",
                "created_at": row[6] or 0,
                "last_activity_at": row[7] or 0,
                "hidden": bool(row[8]),
            })
    finally:
        con.close()
    return sessions


def _scan_transcripts_dir(transcripts_dir: str) -> List[dict]:
    """兜底：扫描 transcripts 目录列出全部会话。"""
    sessions: List[dict] = []
    if not os.path.isdir(transcripts_dir):
        return sessions
    for fname in os.listdir(transcripts_dir):
        if not fname.endswith(".json"):
            continue
        session_id = fname[: -len(".json")]
        if not _SESSION_ID_RE.match(session_id):
            continue
        path = os.path.join(transcripts_dir, fname)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        sessions.append({
            "session_id": session_id,
            "path": path,
            "cwd": "",
            "model": "",
            "title": "",
            "agent_mode": "",
            "backend_type": "",
            "created_at": 0,
            "last_activity_at": int(mtime),
            "hidden": False,
        })
    sessions.sort(key=lambda s: s["last_activity_at"], reverse=True)
    return sessions


def list_running_sessions(devin_home: Optional[str] = None) -> List[dict]:
    """列出运行中会话（从 session_locks 推断）。

    Returns:
        [{session_id, pid}]
    """
    _, _, locks_dir = _devin_dirs(devin_home)
    result: List[dict] = []
    if not os.path.isdir(locks_dir):
        return result
    for fname in os.listdir(locks_dir):
        if not fname.endswith(".lock"):
            continue
        session_id = fname[: -len(".lock")]
        if not _SESSION_ID_RE.match(session_id):
            continue
        lock_path = os.path.join(locks_dir, fname)
        try:
            with open(lock_path, "r") as f:
                pid_str = f.read().strip()
            pid = int(pid_str) if pid_str else None
            # 验证进程是否存活（Windows 用 OpenProcess，POSIX 用 kill(0)）
            if pid is not None and not _process_alive(pid):
                pid = None  # 进程已退出
            result.append({
                "session_id": session_id,
                "pid": pid,
                "path": lock_path,
            })
        except (OSError, ValueError) as e:
            _log.warning("failed to read lock file %s: %s", lock_path, e)
    return result