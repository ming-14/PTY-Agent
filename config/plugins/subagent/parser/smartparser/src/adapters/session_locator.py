"""会话定位适配器：在 temp 目录下搜索 smartagent 会话 JSONL。

JSONL 位置：<temp>/smartagent_subagent/<sid>.jsonl（跨平台 tempfile）
"""
from __future__ import annotations

import os
import tempfile
from typing import List, Optional

from ..infra.logging import get_logger

_log = get_logger("session_locator")

_SUBAGENT_DIR = "smartagent_subagent"
_JSONL_EXT = ".jsonl"


def _base_dir() -> str:
    return os.path.join(tempfile.gettempdir(), _SUBAGENT_DIR)


def find_session_file(session_id: str, data_dir: Optional[str] = None) -> str:
    """按 sessionId 定位 jsonl 文件。

    Args:
        session_id: 会话 UUID
        data_dir: 可选的根目录（缺省用 temp 默认）

    Returns:
        jsonl 文件路径

    Raises:
        FileNotFoundError: 未找到
    """
    base = data_dir or _base_dir()
    path = os.path.join(base, session_id + _JSONL_EXT)
    if os.path.isfile(path):
        return path
    # 兜底搜索
    if os.path.isdir(base):
        for fname in os.listdir(base):
            if fname.endswith(_JSONL_EXT) and fname.startswith(session_id):
                return os.path.join(base, fname)
    raise FileNotFoundError(f"smartagent session jsonl not found: {session_id}")


def find_all_sessions(data_dir: Optional[str] = None) -> List[dict]:
    """列出 temp 目录下全部 smartagent 会话。"""
    base = data_dir or _base_dir()
    sessions: List[dict] = []
    if not os.path.isdir(base):
        return sessions
    for fname in os.listdir(base):
        if not fname.endswith(_JSONL_EXT):
            continue
        session_id = fname[: -len(_JSONL_EXT)]
        path = os.path.join(base, fname)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        sessions.append({
            "session_id": session_id,
            "path": path,
            "mtime": mtime,
        })
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions