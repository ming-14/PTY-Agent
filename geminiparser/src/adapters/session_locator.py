"""会话定位适配器：在 ~/.gemini 下搜索会话 jsonl 文件。

Gemini CLI 存储布局：
- ~/.gemini/tmp/<project>/chats/session-<ts>-<uuid8>.jsonl — 消息历史
- ~/.gemini/tmp/<project>/chats/<main-uuid>/<subagent-uuid>.jsonl — 子代理会话
- ~/.gemini/projects.json — 项目名 → cwd 映射
- 运行中会话：无独立索引（无 sessions/<pid>.json）

会话 id 从文件名定位：文件名最后一段（8位）= UUID 前 8 位。
"""
from __future__ import annotations

import glob as _glob
import json
import os
import re
from typing import Dict, List, Optional

from ..infra.logging import get_logger

_log = get_logger("session_locator")

DEFAULT_GEMINI_DIR = os.path.join(os.path.expanduser("~"), ".gemini")
_TMP_DIR = "tmp"
_CHATS_DIR = "chats"
_SESSION_FILE_RE = re.compile(r"^session-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-[a-f0-9]{8}\.jsonl$")


def _gemini_dirs(gemini_dir: Optional[str]) -> str:
    """返回 tmp 目录。"""
    base = gemini_dir or DEFAULT_GEMINI_DIR
    return os.path.join(base, _TMP_DIR)


def _load_projects(gemini_dir: Optional[str] = None) -> Dict[str, str]:
    """加载 projects.json（cwd → 项目名 映射）。"""
    base = gemini_dir or DEFAULT_GEMINI_DIR
    path = os.path.join(base, "projects.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {v: k for k, v in data.get("projects", {}).items()}
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("failed to load projects.json: %s", e)
        return {}


def _uuid_from_filename(fname: str) -> Optional[str]:
    """从文件名提取 UUID 前缀（前 8 位），返回完整 UUID 候选。"""
    m = _SESSION_FILE_RE.match(fname)
    if not m:
        return None
    # 文件名最后一段（- 分割的最后一段，不含 .jsonl）
    last_part = fname.replace(".jsonl", "").rsplit("-", 1)[-1]
    return last_part


def _read_session_id(path: str) -> Optional[str]:
    """从 JSONL 文件首行读取 sessionId。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            line = f.readline().strip()
            if not line:
                return None
            ev = json.loads(line)
            return ev.get("sessionId")
    except (json.JSONDecodeError, OSError, IndexError) as e:
        _log.debug("failed to read session id from %s: %s", path, e)
        return None


def find_session_file(session_id: str, gemini_dir: Optional[str] = None) -> str:
    """在 tmp 目录下搜索指定 sessionId 的 jsonl 文件。

    搜索策略：
    1. 按文件名 UUID 前缀匹配加速
    2. 读首行 sessionId 精确匹配

    Args:
        session_id: 会话 UUID（如 00000000-0000-0000-0000-000000000001）
        gemini_dir: ~/.gemini 路径，None 则用默认

    Returns:
        jsonl 文件路径

    Raises:
        FileNotFoundError: 未找到
    """
    tmp_dir = _gemini_dirs(gemini_dir)
    if not os.path.isdir(tmp_dir):
        raise FileNotFoundError(f"tmp dir not found: {tmp_dir}")

    uuid_prefix = session_id.split("-")[0] if "-" in session_id else session_id[:8]

    # 遍历所有 project 目录
    for proj in os.listdir(tmp_dir):
        chats_dir = os.path.join(tmp_dir, proj, _CHATS_DIR)
        if not os.path.isdir(chats_dir):
            continue
        # 按文件名前缀匹配加速
        for fname in os.listdir(chats_dir):
            if not fname.endswith(".jsonl"):
                continue
            if uuid_prefix not in fname:
                continue
            path = os.path.join(chats_dir, fname)
            sid = _read_session_id(path)
            if sid == session_id:
                return path

    # 精确匹配所有文件（慢路径）
    for proj in os.listdir(tmp_dir):
        chats_dir = os.path.join(tmp_dir, proj, _CHATS_DIR)
        if not os.path.isdir(chats_dir):
            continue
        for fname in os.listdir(chats_dir):
            if not fname.endswith(".jsonl"):
                continue
            path = os.path.join(chats_dir, fname)
            sid = _read_session_id(path)
            if sid == session_id:
                return path

    raise FileNotFoundError(f"session jsonl not found: {session_id}")


def find_all_sessions(gemini_dir: Optional[str] = None) -> List[dict]:
    """列出 tmp 目录下全部会话。

    Returns:
        [{session_id, path, project, mtime}]，按最后修改时间倒序
    """
    tmp_dir = _gemini_dirs(gemini_dir)
    sessions: List[dict] = []
    if not os.path.isdir(tmp_dir):
        return sessions

    for proj in os.listdir(tmp_dir):
        chats_dir = os.path.join(tmp_dir, proj, _CHATS_DIR)
        if not os.path.isdir(chats_dir):
            continue
        for fname in os.listdir(chats_dir):
            if not fname.endswith(".jsonl"):
                continue
            path = os.path.join(chats_dir, fname)
            sid = _read_session_id(path)
            if not sid:
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0
            sessions.append({
                "session_id": sid,
                "path": path,
                "project": proj,
                "mtime": mtime,
            })

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def list_running_sessions(gemini_dir: Optional[str] = None) -> List[dict]:
    """列出运行中会话。

    Gemini CLI 无运行中会话索引，通过 logs.json 最近活跃推断。
    返回最近 1 小时内有活跃记录的会话列表。

    Returns:
        [{session_id, project, last_activity}]，按活动时间倒序
    """
    tmp_dir = _gemini_dirs(gemini_dir)
    result: List[dict] = []
    now = _dt.datetime.now().timestamp() * 1000
    one_hour = 3600 * 1000

    import datetime as _dt  # noqa: F811

    for proj in os.listdir(tmp_dir):
        logs_path = os.path.join(tmp_dir, proj, "logs.json")
        if not os.path.isfile(logs_path):
            continue
        try:
            with open(logs_path, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(logs, list):
            continue
        # 最近活动的 session
        seen: Dict[str, str] = {}
        for entry in logs:
            sid = entry.get("sessionId", "")
            ts = entry.get("timestamp", "")
            if sid and ts:
                seen[sid] = ts
        for sid, ts in seen.items():
            try:
                dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if (now - dt.timestamp() * 1000) <= one_hour:
                    result.append({
                        "session_id": sid,
                        "project": proj,
                        "last_activity": ts,
                    })
            except (ValueError, TypeError):
                continue

    result.sort(key=lambda s: s.get("last_activity", ""), reverse=True)
    return result