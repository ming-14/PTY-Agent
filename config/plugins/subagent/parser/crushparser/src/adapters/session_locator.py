"""会话定位适配器：在 crush 本地存储中搜索会话。

Crush 存储布局（按项目隔离）：
- %CRUSH_GLOBAL_DATA%/projects.json — 项目注册表（path → data_dir）
- <project>/.crush/crush.db — 项目 SQLite 数据库

会话定位策略：
1. 用户显式指定 data_dir（--data-dir）：只查该目录的 crush.db
2. 否则读取 projects.json 全部项目的 data_dir，逐个查 crush.db
3. 每个库内按会话 ID 匹配：UUID 直查 / XXH3-128 全 hash / hash 前缀

会话 ID 形态：
- UUID：716187c5-046d-46a4-b8ce-00a33067b620
- hash：XXH3-128(id) 的 32 位 hex 字符串（前 7 位用于命令行展示）
- agent tool 会话：<messageID>$$<toolCallID>（内部使用，可过滤）
- title 会话：title-<parentSessionID>（内部使用，可过滤）
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Dict, List, Optional, Tuple

from ..infra.logging import get_logger

_log = get_logger("session_locator")

_DB_NAME = "crush.db"
_PROJECTS_FILE = "projects.json"

# CRUSH_GLOBAL_DATA 指向 data 目录（含 projects.json / crush.json / crush.json.lock）
_GLOBAL_DATA_ENV = "CRUSH_GLOBAL_DATA"
# 兜底：Linux/macOS 的 XDG 数据目录
_XDG_DATA_HOME = "XDG_DATA_HOME"


def global_data_dir() -> str:
    """返回 Crush 全局数据目录（含 projects.json）。

    按优先级探测：
    1. CRUSH_GLOBAL_DATA 环境变量（CRUSH.ps1 设置，如 %USERPROFILE%\\__crush\\data）
    2. XDG_DATA_HOME/crush
    3. Windows %LOCALAPPDATA%/crush（Crush 源码默认）
    4. 本机惯例 %USERPROFILE%/__crush/data
    5. ~/.local/share/crush
    """
    candidates: List[str] = []
    if env := os.environ.get(_GLOBAL_DATA_ENV):
        candidates.append(env)
    if xdg := os.environ.get(_XDG_DATA_HOME):
        candidates.append(os.path.join(xdg, "crush"))
    if local := os.environ.get("LOCALAPPDATA"):
        candidates.append(os.path.join(local, "crush"))
    candidates.append(os.path.join(os.path.expanduser("~"), "__crush", "data"))
    candidates.append(os.path.join(os.path.expanduser("~"), ".local", "share", "crush"))

    # 返回第一个存在 projects.json 的目录；都不存在则返回首个候选
    for c in candidates:
        if os.path.isfile(os.path.join(c, _PROJECTS_FILE)):
            return c
    return candidates[0] if candidates else os.path.join(
        os.path.expanduser("~"), "__crush", "data"
    )


def _projects_file_path() -> str:
    return os.path.join(global_data_dir(), _PROJECTS_FILE)


def _default_data_dirs() -> List[str]:
    """从 projects.json 读取全部项目的 data_dir，按 last_accessed 倒序。"""
    path = _projects_file_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("failed to parse projects.json: %s", e)
        return []
    projects = data.get("projects", []) or []
    # last_accessed 倒序（最近的在前）
    projects.sort(
        key=lambda p: p.get("last_accessed", ""),
        reverse=True,
    )
    dirs: List[str] = []
    for p in projects:
        dd = p.get("data_dir")
        if dd and os.path.isdir(dd):
            dirs.append(dd)
    return dirs


def _db_paths(data_dir: Optional[str]) -> List[str]:
    """返回待搜索的 crush.db 路径列表。"""
    if data_dir:
        p = os.path.join(data_dir, _DB_NAME)
        return [p] if os.path.isfile(p) else []
    return [os.path.join(d, _DB_NAME) for d in _default_data_dirs()
            if os.path.isfile(os.path.join(d, _DB_NAME))]


def open_db(db_path: str) -> sqlite3.Connection:
    """打开 crush.db（只读）。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def hash_session_id(session_id: str) -> str:
    """计算会话 ID 的 XXH3-128 hash（32 位 hex，与 Go zeebo/xxh3 一致）。

    crush 的 session.HashID 用 github.com/zeebo/xxh3 计算：
    - xxh3.New() 为 128-bit digest
    - Sum(nil) 返回 16 字节 → hex 32 字符
    """
    import xxhash
    return xxhash.xxh3_128(session_id.encode("utf-8")).hexdigest()


def _resolve_in_db(db_path: str, session_id: str) -> Optional[dict]:
    """在单个 crush.db 中按 ID 解析会话。

    匹配顺序：UUID 直查 → 全 hash → hash 前缀（歧义报多匹配）。
    """
    con = open_db(db_path)
    try:
        row = con.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is not None:
            d = dict(row)
            d["data_dir"] = os.path.dirname(db_path)
            return d

        # hash 匹配：遍历全部会话计算 XXH3
        try:
            target_hash = hash_session_id(session_id)
        except ImportError:
            _log.warning("xxhash not installed, hash prefix matching disabled")
            return None

        rows = con.execute("SELECT id FROM sessions").fetchall()
        matches: List[str] = []
        for r in rows:
            sid = r["id"]
            h = hash_session_id(sid)
            if h == target_hash or h.startswith(session_id):
                matches.append(sid)
        if len(matches) == 1:
            row = con.execute("SELECT * FROM sessions WHERE id = ?", (matches[0],)).fetchone()
            d = dict(row)
            d["data_dir"] = os.path.dirname(db_path)
            return d
        if len(matches) > 1:
            _log.warning("session ID %r ambiguous in %s: %s",
                         session_id, db_path, matches)
        return None
    finally:
        con.close()


def find_session(session_id: str, data_dir: Optional[str] = None) -> Optional[dict]:
    """按会话 ID 跨库定位会话。

    Args:
        session_id: 会话 ID（UUID / 全 hash / hash 前缀）
        data_dir: 显式数据目录，None 则遍历 projects.json

    Returns:
        会话 dict（含 data_dir 字段）或 None
    """
    for db_path in _db_paths(data_dir):
        d = _resolve_in_db(db_path, session_id)
        if d is not None:
            return d
    return None


def find_all_sessions(data_dir: Optional[str] = None) -> List[dict]:
    """列出全部会话（跨项目库，按 updated_at 倒序）。

    Returns:
        [{session_id, uuid, title, data_dir, message_count, created_at, updated_at, ...}]
    """
    sessions: List[dict] = []
    for db_path in _db_paths(data_dir):
        con = open_db(db_path)
        try:
            for row in con.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall():
                d = dict(row)
                d["data_dir"] = os.path.dirname(db_path)
                # 兼容 CLI 期望的字段名
                d["session_id"] = d["id"]
                d["uuid"] = d["id"]
                sessions.append(d)
        finally:
            con.close()
    sessions.sort(key=lambda s: s.get("updated_at") or 0, reverse=True)
    return sessions


def list_running_sessions(data_dir: Optional[str] = None) -> List[dict]:
    """列出运行中会话。

    Crush 无 PID 索引文件，判定依据：最近 updated_at 的会话（排除内部
    agent tool / title 会话）最可能是当前活跃会话。此函数返回全部
    非内部会话按 updated_at 倒序，由上层决定活跃候选。
    """
    sessions = []
    for s in find_all_sessions(data_dir):
        sid = s["id"]
        if _is_internal_session(sid):
            continue
        sessions.append({
            "session_id": sid,
            "uuid": sid,
            "title": s.get("title") or "",
            "data_dir": s.get("data_dir") or "",
            "updated_at": s.get("updated_at") or 0,
        })
    return sessions


def _is_internal_session(session_id: str) -> bool:
    """判断是否为内部会话（agent tool 子会话 / title 生成会话）。"""
    return "$$" in session_id or session_id.startswith("title-")


def resolve_session_id(session_id: str, data_dir: Optional[str] = None) -> str:
    """把会话 ID 解析为规范 UUID（subagent 消息读取的定位入口）

    Crush 的会话记录分布在多个项目库中，消息加载需要精确的会话 UUID，
    传入的可能是命令行展示用的 hash 前缀，故在此统一解析。

    Args:
        session_id: 会话 ID（UUID / XXH3 全 hash / hash 前缀）
        data_dir: 显式数据目录，None 则遍历 projects.json

    Returns:
        会话的规范 ID（UUID）

    Raises:
        FileNotFoundError: 数据目录尚未创建 crush.db（agent 刚启动）
        KeyError: 会话不存在
    """
    db_paths = _db_paths(data_dir)
    if not db_paths:
        raise FileNotFoundError(f"crush.db not found (data_dir={data_dir})")
    for db_path in db_paths:
        d = _resolve_in_db(db_path, session_id)
        if d is not None:
            return d["id"]
    raise KeyError(f"session not found: {session_id}")


def find_latest_session(prompt: str = "", data_dir: Optional[str] = None) -> Optional[str]:
    """发现最新的非内部会话 ID（subagent 会话发现入口）

    Crush 自动分配 UUID 会话 ID 且无 PID 索引，交互模式无法预设会话 ID，
    只能启动后从数据目录反查。数据目录隔离时（每个 spawn 独立 --data-dir）
    库内会话唯一，取最近更新者即为本次会话。

    Args:
        prompt: 不使用（Crush 会话不记录初始 prompt，无法按内容匹配）；
            保留以符合 discover 契约的函数签名
        data_dir: 显式数据目录，None 则遍历 projects.json

    Returns:
        会话 ID；尚无会话时返回 None
    """
    for s in list_running_sessions(data_dir):
        return s["session_id"]
    return None