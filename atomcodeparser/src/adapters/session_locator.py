"""会话定位适配器：在 $ATOMCODE_HOME 下搜索会话文件与元数据。

AtomCode 存储布局：
- sessions/<cwd-hash>/<sessionId>.jsonl — 消息历史
- sessions/<cwd-hash>/<sessionId>.meta   — 会话元数据（含 working_dir）
- sessions/<cwd-hash>/<sessionId>.snapshot — 完整消息快照
- sessions/<cwd-hash>/<sessionId>.rewind.json — 回退点
- sessions/<cwd-hash>/<sessionId>.ui.json — UI 状态

cwd 编码：16 位十六进制哈希（64 位），算法未公开。
解析器不反向计算哈希，而是遍历 sessions/ 子目录 + 读取 .meta.working_dir。
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

from ..entities import Session, Usage
from ..infra.logging import get_logger

_log = get_logger("session_locator")

# 默认数据目录：$ATOMCODE_HOME 优先，其次 ~/.atomcode
def _default_data_dir() -> str:
    home = os.environ.get("ATOMCODE_HOME", "")
    if home:
        return home
    return os.path.join(os.path.expanduser("~"), ".atomcode")


# sessionId 形如 UUID：8-4-4-4-12 十六进制
_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# 文件名模式：<sessionId>.jsonl / .meta / .snapshot / .rewind.json / .ui.json
_SESSION_FNAME_RE = re.compile(
    r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.(jsonl|meta|snapshot|rewind\.json|ui\.json)$"
)


def _data_dir(data_dir: Optional[str]) -> str:
    return data_dir or _default_data_dir()


def _sessions_dir(data_dir: Optional[str]) -> str:
    return os.path.join(_data_dir(data_dir), "sessions")


def find_session_dir(session_id: str, data_dir: Optional[str] = None) -> str:
    """定位包含指定 sessionId 的目录（cwd-hash 目录）。

    Args:
        session_id: 会话 UUID
        data_dir: $ATOMCODE_HOME 路径，None 则用默认

    Returns:
        会话所在目录路径（sessions/<cwd-hash>）

    Raises:
        FileNotFoundError: 未找到
    """
    base = _sessions_dir(data_dir)
    if not os.path.isdir(base):
        raise FileNotFoundError(f"sessions dir not found: {base}")

    for cwd_hash in os.listdir(base):
        cwd_path = os.path.join(base, cwd_hash)
        if not os.path.isdir(cwd_path):
            continue
        if os.path.isfile(os.path.join(cwd_path, f"{session_id}.jsonl")) or \
           os.path.isfile(os.path.join(cwd_path, f"{session_id}.meta")):
            return cwd_path
    raise FileNotFoundError(f"session not found: {session_id}")


def find_session_file(session_id: str, data_dir: Optional[str] = None,
                      ext: str = "jsonl") -> str:
    """定位指定 sessionId 的文件路径。

    Args:
        session_id: 会话 UUID
        data_dir: $ATOMCODE_HOME 路径
        ext: 文件扩展名（jsonl / meta / snapshot / rewind.json / ui.json）

    Returns:
        文件完整路径

    Raises:
        FileNotFoundError: 未找到
    """
    cwd_path = find_session_dir(session_id, data_dir)
    path = os.path.join(cwd_path, f"{session_id}.{ext}")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"session file not found: {path}")
    return path


def load_meta_file(session_id: str, data_dir: Optional[str] = None) -> Dict:
    """读取会话元数据 .meta 文件。

    Returns:
        元数据 dict；文件不存在时返回空 dict
    """
    try:
        path = find_session_file(session_id, data_dir, ext="meta")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        _log.warning("failed to load meta for %s: %s", session_id, e)
        return {}


def find_all_sessions(data_dir: Optional[str] = None) -> List[dict]:
    """列出 sessions/ 下全部会话。

    Returns:
        [{session_id, path, cwd, cwd_hash, name, mtime, turn_count}]，
        按最后修改时间倒序
    """
    base = _sessions_dir(data_dir)
    sessions: List[dict] = []
    if not os.path.isdir(base):
        return sessions

    for cwd_hash in os.listdir(base):
        cwd_path = os.path.join(base, cwd_hash)
        if not os.path.isdir(cwd_path):
            continue
        for fname in os.listdir(cwd_path):
            m = _SESSION_FNAME_RE.match(fname)
            if not m or m.group(2) != "meta":
                continue
            session_id = m.group(1)
            path = os.path.join(cwd_path, fname)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0
            meta = {}
            try:
                with open(path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
            sessions.append({
                "session_id": session_id,
                "path": path,
                "cwd": meta.get("working_dir", ""),
                "cwd_hash": cwd_hash,
                "name": meta.get("name", ""),
                "turn_count": meta.get("turn_count", 0),
                "mtime": mtime,
            })

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def list_running_sessions(data_dir: Optional[str] = None) -> List[dict]:
    """列出可能的运行中会话。

    AtomCode 无专用运行索引（.lease 为空文件）。策略：
    - 遍历 sessions/ 下全部会话，取 .snapshot 中 turn_counter > 0 且
      .lease 文件存在的会话（可能仍在运行）
    - 注意：此判断不精确，进程退出后 .lease 仍可能残留

    Returns:
        [{session_id, cwd, cwd_hash, last_updated, turn_count}]，按更新时间倒序
    """
    base = _sessions_dir(data_dir)
    result: List[dict] = []
    if not os.path.isdir(base):
        return result

    for cwd_hash in os.listdir(base):
        cwd_path = os.path.join(base, cwd_hash)
        if not os.path.isdir(cwd_path):
            continue
        for fname in os.listdir(cwd_path):
            m = _SESSION_FNAME_RE.match(fname)
            if not m:
                continue
            session_id, ext = m.group(1), m.group(2)
            if ext != "meta":
                continue
            meta_path = os.path.join(cwd_path, fname)
            lease_path = os.path.join(cwd_path, f"{session_id}.lease")
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            # 有 lease 文件的会话视为可能在运行
            if not os.path.isfile(lease_path):
                continue
            result.append({
                "session_id": session_id,
                "cwd": meta.get("working_dir", ""),
                "cwd_hash": cwd_hash,
                "last_updated": meta.get("updated_at", 0),
                "turn_count": meta.get("turn_count", 0),
            })

    result.sort(key=lambda s: s["last_updated"], reverse=True)
    return result