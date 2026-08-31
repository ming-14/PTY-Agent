"""会话定位适配器：在 ~/.claude 下搜索会话 jsonl 与元数据。

Claude Code 存储布局：
- ~/.claude/projects/<cwd-encoded>/<sessionId>.jsonl — 消息历史
- ~/.claude/sessions/<pid>.json — 运行中会话索引（pid → sessionId）

cwd 编码规则：`C:\\Users\\<user>` → `C--Users-<user>`
（盘符冒号 → `-`，路径分隔符 → `-`）
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

from ..entities import Session, Usage
from ..infra.logging import get_logger

_log = get_logger("session_locator")

DEFAULT_CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")
_PROJECTS_DIR = "projects"
_SESSIONS_DIR = "sessions"

# sessionId 形如 UUID：8-4-4-4-12 十六进制
_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _claude_dirs(claude_dir: Optional[str]) -> Tuple[str, str]:
    """返回 (projects_dir, sessions_dir)。"""
    base = claude_dir or DEFAULT_CLAUDE_DIR
    return os.path.join(base, _PROJECTS_DIR), os.path.join(base, _SESSIONS_DIR)


def find_session_file(session_id: str, claude_dir: Optional[str] = None) -> str:
    """在 projects 目录下搜索指定 sessionId 的 jsonl 文件。

    Args:
        session_id: 会话 UUID
        claude_dir: ~/.claude 路径，None 则用默认

    Returns:
        jsonl 文件路径

    Raises:
        FileNotFoundError: 未找到
    """
    projects_dir, _ = _claude_dirs(claude_dir)
    if not os.path.isdir(projects_dir):
        raise FileNotFoundError(f"projects dir not found: {projects_dir}")

    filename = f"{session_id}.jsonl"
    # 直接按编码目录查找（常见情况）
    for root, dirs, files in os.walk(projects_dir):
        if filename in files:
            return os.path.join(root, filename)
    raise FileNotFoundError(f"session jsonl not found: {session_id}")


def find_all_sessions(claude_dir: Optional[str] = None) -> List[dict]:
    """列出 projects 目录下全部会话。

    Returns:
        [{session_id, path, cwd}]，按最后修改时间倒序
    """
    projects_dir, _ = _claude_dirs(claude_dir)
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


def load_running_session(pid: int, claude_dir: Optional[str] = None) -> Optional[dict]:
    """从 sessions/<pid>.json 读取运行中会话索引。

    Returns:
        索引 dict（含 sessionId/cwd/status 等），文件不存在返回 None
    """
    _, sessions_dir = _claude_dirs(claude_dir)
    path = os.path.join(sessions_dir, f"{pid}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("failed to load session index %s: %s", path, e)
        return None


def list_running_sessions(claude_dir: Optional[str] = None) -> List[dict]:
    """列出 sessions/ 目录下全部运行中会话索引。

    Returns:
        [{pid, sessionId, cwd, status, startedAt}]，按 startedAt 倒序
    """
    _, sessions_dir = _claude_dirs(claude_dir)
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
        idx = load_running_session(pid, claude_dir)
        if idx:
            idx["pid"] = pid
            result.append(idx)
    result.sort(key=lambda s: s.get("startedAt", 0), reverse=True)
    return result


def encode_cwd(cwd: str) -> str:
    """cwd → projects 目录名（盘符冒号与路径分隔符替换为 `-`）。"""
    return re.sub(r"[\\/:]", "-", cwd).replace("-", "-")
