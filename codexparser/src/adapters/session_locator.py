"""会话定位适配器：在 ~/.codex 下搜索 rollout 文件。

Codex 存储布局：
- ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl — 消息历史
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from ..infra.logging import get_logger

_log = get_logger("session_locator")

DEFAULT_CODEX_HOME = os.path.join(os.path.expanduser("~"), ".codex")
_SESSIONS_DIR = "sessions"

# rollout 文件名：rollout-<ISO-timestamp>-<uuid>.jsonl
_ROLLOUT_RE = re.compile(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-([0-9a-fA-F-]+)\.jsonl$")


def _sessions_dir(codex_home: Optional[str]) -> str:
    return os.path.join(codex_home or DEFAULT_CODEX_HOME, _SESSIONS_DIR)


def find_rollout_file(session_id: str, codex_home: Optional[str] = None) -> str:
    """在 sessions 目录下搜索指定 session_id 的 rollout jsonl 文件。

    session_id 即 rollout 文件名中的 UUID 部分（如 01a02a54-...）。

    Args:
        session_id: 会话 UUID
        codex_home: ~/.codex 路径，None 则用默认

    Returns:
        rollout jsonl 文件路径

    Raises:
        FileNotFoundError: 未找到
    """
    base = _sessions_dir(codex_home)
    if not os.path.isdir(base):
        raise FileNotFoundError(f"sessions dir not found: {base}")

    # 按文件名模式的 UUID 匹配
    expected = f"rollout-*-{session_id}.jsonl"
    import glob
    candidates = glob.glob(os.path.join(base, "**", expected), recursive=True)
    if candidates:
        return candidates[0]

    # 兜底：遍历全部 rollout 文件，检查 payload 中 id
    for root, dirs, files in os.walk(base):
        for fname in files:
            m = _ROLLOUT_RE.match(fname)
            if m and m.group(1) == session_id:
                return os.path.join(root, fname)

    raise FileNotFoundError(f"rollout not found: {session_id}")


def find_all_sessions(codex_home: Optional[str] = None) -> List[dict]:
    """列出 sessions 目录下全部会话。

    Returns:
        [{session_id, path, mtime}]，按最后修改时间倒序
    """
    base = _sessions_dir(codex_home)
    sessions: List[dict] = []
    if not os.path.isdir(base):
        return sessions

    for root, dirs, files in os.walk(base):
        for fname in files:
            m = _ROLLOUT_RE.match(fname)
            if not m:
                continue
            path = os.path.join(root, fname)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0
            sessions.append({
                "session_id": m.group(1),
                "path": path,
                "mtime": mtime,
            })

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions