r"""会话定位适配器：在 Pi 数据目录中发现会话文件。

Pi 会话存储于 <agentDir>/sessions/--<encoded-cwd>--/<timestamp>_<uuid>.jsonl：
- agentDir 由 PI_CODING_AGENT_DIR 环境变量控制，默认 ~/.pi/agent
- cwd 编码：C:\Users\<user> → --C--Users-<user>--（分隔符与冒号统一替换为 -）
- 每个会话是一个 JSONL 文件，首行 header 含 id/cwd/timestamp

无 SQLite 索引、无 pid 锁文件（与 Devin/WorkBuddy 不同），
会话发现需遍历目录读 header（每文件只读前几行）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any, Dict, List, Optional

from ..infra.logging import get_logger

_log = get_logger("session_locator")

# 默认 agent 数据目录（%USERPROFILE%\.pi\agent）
_DEFAULT_AGENT_DIR = os.path.join(os.path.expanduser("~"), ".pi", "agent")

# 环境变量（与 Pi 的 config.ts ENV_AGENT_DIR 一致）
_ENV_AGENT_DIR = "PI_CODING_AGENT_DIR"
_ENV_SESSION_DIR = "PI_CODING_AGENT_SESSION_DIR"

# 运行中判定：文件 mtime 新鲜度窗口（秒）
_RUNNING_FRESHNESS_S = 120

# cwd 编码目录：--...--（首尾双横线，中间为替换后的路径）
_ENCODED_DIR_RE = re.compile(r"^--.+--$")


def get_agent_dir() -> str:
    """获取 Pi agent 数据目录（环境变量优先，默认 ~/.pi/agent）。"""
    env_dir = os.environ.get(_ENV_AGENT_DIR)
    if env_dir:
        return os.path.expandvars(os.path.expanduser(env_dir))
    return _DEFAULT_AGENT_DIR


def get_sessions_dir(agent_dir: Optional[str] = None) -> str:
    """获取会话根目录。"""
    agent_dir = agent_dir or get_agent_dir()
    env_session = os.environ.get(_ENV_SESSION_DIR)
    if env_session:
        return os.path.expandvars(os.path.expanduser(env_session))
    return os.path.join(agent_dir, "sessions")


def encode_cwd(cwd: str) -> str:
    """将工作目录编码为会话目录名（与 Pi 的 getDefaultSessionDirPath 一致）。"""
    resolved = os.path.normpath(cwd)
    # 去前导分隔符/盘符冒号，替换分隔符与冒号为 -
    stripped = re.sub(r"^[/\\]", "", resolved)
    safe = re.sub(r"[/\\:]", "-", stripped)
    return f"--{safe}--"


def _read_header(path: str) -> Optional[dict]:
    """读取 JSONL 首行 header（只读前 64KB）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            while True:
                line = f.readline()
                if not line:
                    return None
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if data.get("type") == "session" and isinstance(data.get("id"), str):
                    return data
                return None
    except Exception as e:
        _log.warning("failed to read header %s: %s", path, e)
        return None


def find_all_sessions(agent_dir: Optional[str] = None) -> List[dict]:
    """列出全部会话（跨所有 cwd 目录）。

    返回按 modified 降序排列的会话信息列表：
    { session_id, cwd, title, model, created, modified, message_count, first_message, path }
    """
    sessions_dir = get_sessions_dir(agent_dir)
    if not os.path.isdir(sessions_dir):
        return []

    results: List[dict] = []
    try:
        entries = os.listdir(sessions_dir)
    except OSError as e:
        _log.warning("failed to list sessions dir %s: %s", sessions_dir, e)
        return []

    for name in entries:
        dir_path = os.path.join(sessions_dir, name)
        if not os.path.isdir(dir_path):
            continue
        try:
            files = [f for f in os.listdir(dir_path) if f.endswith(".jsonl")]
        except OSError:
            continue
        for fname in files:
            path = os.path.join(dir_path, fname)
            header = _read_header(path)
            if header is None:
                continue
            try:
                stat = os.stat(path)
                modified_ms = int(stat.st_mtime * 1000)
                created_ms = int(stat.st_ctime * 1000)
            except OSError:
                modified_ms = created_ms = 0

            info = {
                "session_id": header.get("id", ""),
                "cwd": header.get("cwd", ""),
                "title": "",
                "model": "",
                "created": created_ms,
                "modified": modified_ms,
                "message_count": 0,
                "first_message": "",
                "path": path,
            }
            results.append(info)

    results.sort(key=lambda s: s["modified"], reverse=True)
    return results


def find_session_file(session_id: str, agent_dir: Optional[str] = None) -> Optional[str]:
    """按会话 ID（UUID 前缀匹配）定位会话文件路径。"""
    sessions_dir = get_sessions_dir(agent_dir)
    if not os.path.isdir(sessions_dir):
        return None

    best: Optional[str] = None
    try:
        entries = os.listdir(sessions_dir)
    except OSError:
        return None

    for name in entries:
        dir_path = os.path.join(sessions_dir, name)
        if not os.path.isdir(dir_path):
            continue
        try:
            files = [f for f in os.listdir(dir_path) if f.endswith(".jsonl")]
        except OSError:
            continue
        for fname in files:
            path = os.path.join(dir_path, fname)
            header = _read_header(path)
            if header is None:
                continue
            sid = header.get("id", "")
            if sid == session_id:
                return path
            # 前缀匹配（Pi 的 --session <id> 支持部分 ID）
            if session_id and sid.startswith(session_id):
                if best is None:
                    best = path
    return best


def list_running_sessions(agent_dir: Optional[str] = None) -> List[dict]:
    """列出可能运行中的会话。

    Pi 无 pid 锁文件，判定依据：
    1. 存在 pi 进程（pi.exe / pi）
    2. 会话文件 mtime 在新鲜度窗口内（被活跃写入）
    """
    if not _pi_process_running():
        return []
    window = datetime.timedelta(seconds=_RUNNING_FRESHNESS_S)
    now = datetime.datetime.now()
    sessions = find_all_sessions(agent_dir)
    running = []
    for s in sessions:
        modified = s["modified"]
        if modified <= 0:
            continue
        mtime = datetime.datetime.fromtimestamp(modified / 1000.0)
        if now - mtime <= window:
            running.append(s)
    return running


def _pi_process_running() -> bool:
    """检测是否有 pi 进程在运行（跨平台尽力而为）。"""
    try:
        if os.name == "nt":
            result = os.popen('tasklist /FI "IMAGENAME eq pi.exe" /NH').read()
            return "pi.exe" in result
        result = os.popen("pgrep -f 'pi( |$)'").read()
        return bool(result.strip())
    except Exception:
        return False
