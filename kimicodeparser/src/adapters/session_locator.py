r"""会话定位适配器：在 ~/.kimi-code 下搜索会话 wire.jsonl 与元数据。

Kimi Code 存储布局：
- ~/.kimi-code/session_index.jsonl — 会话索引（sessionId → sessionDir + workDir）
- ~/.kimi-code/sessions/<workspaceId>/session_<UUID>/state.json — 会话状态
- ~/.kimi-code/sessions/<workspaceId>/session_<UUID>/agents/main/wire.jsonl — 消息历史

默认目录：~/.kimi-code/（可通过 KIMI_CODE_HOME 环境变量覆盖）。
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

from ..entities import Session, Usage
from ..infra.logging import get_logger

_log = get_logger("session_locator")

DEFAULT_KIMI_DIR = os.path.join(os.path.expanduser("~"), ".kimi-code")
_SESSIONS_DIR = "sessions"
_SESSION_INDEX = "session_index.jsonl"

# session_<UUID> 格式
_SESSION_ID_RE = re.compile(
    r"^session_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _kimi_dir(override: Optional[str]) -> str:
    """返回 kimi 数据目录。"""
    env_dir = os.environ.get("KIMI_CODE_HOME")
    if env_dir:
        return env_dir
    return override or DEFAULT_KIMI_DIR


def _load_session_index(kimi_dir: str) -> List[dict]:
    """加载 session_index.jsonl。"""
    path = os.path.join(kimi_dir, _SESSION_INDEX)
    if not os.path.isfile(path):
        return []
    entries: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        _log.warning("failed to read session index: %s", e)
    return entries


def find_session_file(session_id: str, kimi_dir: Optional[str] = None) -> str:
    """定位指定 sessionId 的 wire.jsonl 文件。

    Args:
        session_id: 会话 ID（如 session_00000000-0000-0000-0000-000000000001）
        kimi_dir: ~/.kimi-code 路径，None 则用默认

    Returns:
        wire.jsonl 文件路径

    Raises:
        FileNotFoundError: 未找到
    """
    base = _kimi_dir(kimi_dir)

    # 1. 从 session_index.jsonl 查询
    for entry in _load_session_index(base):
        if entry.get("sessionId") == session_id:
            session_dir = entry.get("sessionDir", "")
            wire_path = os.path.join(session_dir, "agents", "main", "wire.jsonl")
            if os.path.isfile(wire_path):
                return wire_path
            # 也尝试相对路径
            wire_path_abs = os.path.join(base, session_dir, "agents", "main", "wire.jsonl")
            if os.path.isfile(wire_path_abs):
                return wire_path_abs

    # 2. 遍历 sessions/ 目录
    sessions_dir = os.path.join(base, _SESSIONS_DIR)
    if os.path.isdir(sessions_dir):
        for workspace_id in os.listdir(sessions_dir):
            ws_path = os.path.join(sessions_dir, workspace_id)
            if not os.path.isdir(ws_path):
                continue
            for sess_dir in os.listdir(ws_path):
                if sess_dir == session_id:
                    wire_path = os.path.join(ws_path, sess_dir, "agents", "main", "wire.jsonl")
                    if os.path.isfile(wire_path):
                        return wire_path

    raise FileNotFoundError(f"session wire.jsonl not found: {session_id}")


def _read_state_json(state_path: str) -> Optional[dict]:
    """读取 state.json 返回元数据，失败返回 None。"""
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("failed to read state.json: %s", e)
        return None


def find_all_sessions(kimi_dir: Optional[str] = None) -> List[dict]:
    """列出全部会话。

    Returns:
        [{session_id, path, cwd, title, mtime}]，按更新时间倒序
    """
    base = _kimi_dir(kimi_dir)
    sessions: List[dict] = []

    # 先从 session_index.jsonl 获取
    index_entries = _load_session_index(base)
    indexed_ids = set()

    for entry in index_entries:
        sid = entry.get("sessionId", "")
        if not sid:
            continue
        indexed_ids.add(sid)
        session_dir = entry.get("sessionDir", "")
        # 尝试绝对路径和相对路径
        state_path = os.path.join(session_dir, "state.json")
        if not os.path.isfile(state_path):
            state_path = os.path.join(base, session_dir, "state.json")
        meta = _read_state_json(state_path) if os.path.isfile(state_path) else {}
        sessions.append({
            "session_id": sid,
            "path": os.path.join(session_dir, "agents", "main", "wire.jsonl"),
            "cwd": meta.get("cwd", entry.get("workDir", "")),
            "title": meta.get("title", ""),
            "mtime": meta.get("updatedAt", 0) or 0,
        })

    # 再遍历 sessions/ 目录补充索引中没有的
    sessions_dir = os.path.join(base, _SESSIONS_DIR)
    if os.path.isdir(sessions_dir):
        for workspace_id in os.listdir(sessions_dir):
            ws_path = os.path.join(sessions_dir, workspace_id)
            if not os.path.isdir(ws_path):
                continue
            for sess_dir in os.listdir(ws_path):
                if sess_dir in indexed_ids:
                    continue
                state_path = os.path.join(ws_path, sess_dir, "state.json")
                wire_path = os.path.join(ws_path, sess_dir, "agents", "main", "wire.jsonl")
                if not os.path.isfile(state_path) and not os.path.isfile(wire_path):
                    continue
                meta = _read_state_json(state_path) if os.path.isfile(state_path) else {}
                sessions.append({
                    "session_id": sess_dir,
                    "path": wire_path if os.path.isfile(wire_path) else "",
                    "cwd": meta.get("cwd", meta.get("workDir", "")),
                    "title": meta.get("title", ""),
                    "mtime": meta.get("updatedAt", 0) or 0,
                })

    # 按更新时间倒序（mtime 为 0 的排最后）
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def load_state_meta(session_id: str, kimi_dir: Optional[str] = None) -> Optional[dict]:
    """从 state.json 读取会话元数据。

    Returns:
        dict 或 None（会话不存在）
    """
    base = _kimi_dir(kimi_dir)

    # 从 session_index.jsonl 定位
    for entry in _load_session_index(base):
        if entry.get("sessionId") == session_id:
            session_dir = entry.get("sessionDir", "")
            state_path = os.path.join(session_dir, "state.json")
            if not os.path.isfile(state_path):
                state_path = os.path.join(base, session_dir, "state.json")
            if os.path.isfile(state_path):
                return _read_state_json(state_path)
            break

    # 遍历搜索
    sessions_dir = os.path.join(base, _SESSIONS_DIR)
    if os.path.isdir(sessions_dir):
        for workspace_id in os.listdir(sessions_dir):
            ws_path = os.path.join(sessions_dir, workspace_id)
            if not os.path.isdir(ws_path):
                continue
            state_path = os.path.join(ws_path, session_id, "state.json")
            if os.path.isfile(state_path):
                return _read_state_json(state_path)

    return None