r"""会话定位适配器：在 ~/.codebuddy 下搜索会话 jsonl 与元数据。

cbc（CodeBuddy Code CLI）存储布局：
- ~/.codebuddy/projects/<cwd-encoded>/<sessionId>.jsonl — 消息历史
- ~/.codebuddy/sessions/<pid>.json — 运行中会话索引（pid -> sessionId、kind）
- ~/.codebuddy/workbuddy.db — SQLite 会话元数据索引（title/mode/model/status）

cwd 编码规则：`C:\Users\<user>\Desktop\PTY-Agent` -> `c-Users-<user>-Desktop-PTY-Agent`
（盘符小写，冒号与路径分隔符 -> `-`）

sessionId 两种形态：
- UUID（如 e6e83172-...）-> 有对应 jsonl 文件
- `interactive-<pid>` -> 旧版/主机模式，需通过 sessions/<pid>.json 的 cwd 定位
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Dict, List, Optional, Tuple

from ..entities import Session, Usage
from ..infra.logging import get_logger

_log = get_logger("session_locator")

DEFAULT_WORKBUDDY_DIR = os.path.join(os.path.expanduser("~"), ".codebuddy")
_PROJECTS_DIR = "projects"
_SESSIONS_DIR = "sessions"
_DB_NAME = "workbuddy.db"

# sessionId 形如 UUID：8-4-4-4-12 十六进制
_SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# interactive-<pid> 形态
_INTERACTIVE_RE = re.compile(r"^interactive-(\d+)$")


def _workbuddy_dirs(workbuddy_dir: Optional[str]) -> Tuple[str, str, str]:
    """返回 (projects_dir, sessions_dir, db_path)。"""
    base = workbuddy_dir or DEFAULT_WORKBUDDY_DIR
    return (
        os.path.join(base, _PROJECTS_DIR),
        os.path.join(base, _SESSIONS_DIR),
        os.path.join(base, _DB_NAME),
    )


def encode_cwd(cwd: str) -> str:
    """cwd → projects 目录名（盘符小写，冒号与路径分隔符 → `-`）。"""
    return re.sub(r"[\\/:]", "-", cwd)


def find_session_file(session_id: str, workbuddy_dir: Optional[str] = None) -> str:
    """定位指定 sessionId 的 jsonl 文件。

    Args:
        session_id: 会话 ID（UUID 或 interactive-<pid>）
        workbuddy_dir: ~/.workbuddy 路径，None 则用默认

    Returns:
        jsonl 文件路径

    Raises:
        FileNotFoundError: 未找到
    """
    projects_dir, sessions_dir, _ = _workbuddy_dirs(workbuddy_dir)

    # 1. UUID 形态：直接按文件名搜索
    if _SESSION_ID_RE.match(session_id):
        filename = f"{session_id}.jsonl"
        if os.path.isdir(projects_dir):
            for root, dirs, files in os.walk(projects_dir):
                if filename in files:
                    return os.path.join(root, filename)
        raise FileNotFoundError(f"session jsonl not found: {session_id}")

    # 2. interactive-<pid> 形态：通过 sessions/<pid>.json 的 cwd 定位
    m = _INTERACTIVE_RE.match(session_id)
    if m:
        pid = int(m.group(1))
        idx_path = os.path.join(sessions_dir, f"{pid}.json")
        if os.path.isfile(idx_path):
            try:
                with open(idx_path, "r", encoding="utf-8") as f:
                    idx = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                _log.warning("failed to load session index %s: %s", idx_path, e)
                raise FileNotFoundError(f"session jsonl not found: {session_id}")

            cwd = idx.get("cwd", "")
            if cwd:
                cwd_dir = encode_cwd(cwd)
                if os.path.isdir(os.path.join(projects_dir, cwd_dir)):
                    candidates = [
                        os.path.join(projects_dir, cwd_dir, f)
                        for f in os.listdir(os.path.join(projects_dir, cwd_dir))
                        if f.endswith(".jsonl")
                    ]
                    if candidates:
                        # 取最新修改的 jsonl
                        return max(candidates, key=os.path.getmtime)

        raise FileNotFoundError(f"session jsonl not found: {session_id}")

    raise FileNotFoundError(f"invalid session id: {session_id}")


def find_all_sessions(workbuddy_dir: Optional[str] = None) -> List[dict]:
    """列出 projects 目录下全部会话。

    Returns:
        [{session_id, path, cwd_dir, mtime}]，按最后修改时间倒序
    """
    projects_dir, _, _ = _workbuddy_dirs(workbuddy_dir)
    sessions: List[dict] = []
    if not os.path.isdir(projects_dir):
        return sessions

    for cwd_dir in os.listdir(projects_dir):
        cwd_path = os.path.join(projects_dir, cwd_dir)
        if not os.path.isdir(cwd_path):
            continue
        for fname in os.listdir(cwd_path):
            if not fname.endswith(".jsonl"):
                continue
            session_id = fname[: -len(".jsonl")]
            if not _SESSION_ID_RE.match(session_id):
                continue
            path = os.path.join(cwd_path, fname)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0
            sessions.append({
                "session_id": session_id,
                "path": path,
                "cwd_dir": cwd_dir,
                "mtime": mtime,
            })

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def load_running_session(pid: int, workbuddy_dir: Optional[str] = None) -> Optional[dict]:
    """从 sessions/<pid>.json 读取运行中会话索引。

    Returns:
        索引 dict（含 sessionId/cwd/kind/pid 等），文件不存在返回 None
    """
    _, sessions_dir, _ = _workbuddy_dirs(workbuddy_dir)
    path = os.path.join(sessions_dir, f"{pid}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            idx = json.load(f)
        idx["pid"] = pid
        return idx
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("failed to load session index %s: %s", path, e)
        return None


def list_running_sessions(workbuddy_dir: Optional[str] = None) -> List[dict]:
    """列出 sessions/ 目录下运行中会话索引（过滤 prewarm）。

    Returns:
        [{pid, sessionId, cwd, kind, lastHeartbeat, ...}]，按 startedAt 倒序
    """
    _, sessions_dir, _ = _workbuddy_dirs(workbuddy_dir)
    result: List[dict] = []
    if not os.path.isdir(sessions_dir):
        return result
    for fname in os.listdir(sessions_dir):
        if not fname.endswith(".json"):
            continue
        try:
            pid = int(fname[: -len(".json")])
        except ValueError:
            continue
        idx = load_running_session(pid, workbuddy_dir)
        if not idx:
            continue
        # 过滤 prewarm 预启动进程池
        if idx.get("kind") == "prewarm":
            continue
        result.append(idx)
    result.sort(key=lambda s: s.get("startedAt", 0), reverse=True)
    return result


def load_db_meta(session_id: str, workbuddy_dir: Optional[str] = None) -> Optional[dict]:
    """从 workbuddy.db 读取会话元数据（可选补充）。

    Returns:
        dict 或 None（会话不在库中 / 库不可用）
    """
    _, _, db_path = _workbuddy_dirs(workbuddy_dir)
    if not os.path.isfile(db_path):
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute(
                "SELECT id, cwd, title, custom_title, status, mode, model, "
                "permission_mode, source_mode, created_at, last_activity_at, "
                "use_sandbox_cli FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        finally:
            con.close()
    except (sqlite3.Error, OSError) as e:
        _log.warning("failed to read workbuddy.db: %s", e)
        return None
    if not row:
        return None
    cols = (
        "id", "cwd", "title", "custom_title", "status", "mode", "model",
        "permission_mode", "source_mode", "created_at", "last_activity_at",
        "use_sandbox_cli",
    )
    meta = dict(zip(cols, row))
    # custom_title 优先于 ai-title
    if meta.get("custom_title"):
        meta["title"] = meta["custom_title"]
    return meta
