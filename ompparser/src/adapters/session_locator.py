r"""会话定位适配器：在 omp 数据目录中发现会话文件。

omp 会话存储于 <agentDir>/sessions/<encoded-cwd>/<timestamp>_<uuid>.jsonl：
- agentDir 由 PI_CODING_AGENT_DIR 环境变量控制（OMP.ps1 设为 %USERPROFILE%\__omp\agent）
- cwd 编码规则（与 omp session-paths.ts 一致）：
  - home 下：`-<home-relative>`，如 `C:\Users\<user>\Desktop\ompparser` → `-Desktop-ompparser`；
    cwd == home 时为 `-`
  - temp 下：`-tmp-<relative>`，如 `%TEMP%\foo` → `-tmp-foo`
  - home/temp 外（绝对路径）：`--<encoded-abs>--`，如 `D:\proj` → `--D--proj--`
- 文件名：`<fileSafeTimestamp>_<uuid>.jsonl`，其中 fileSafeTimestamp =
  ISO 时间戳的 `:` 与 `.` 替换为 `-`（如 `2026-08-23T07-39-40-880Z_<uuid>.jsonl`）

无 SQLite 会话索引（agent.db 仅存 auth/settings/usage），
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

# 默认 agent 数据目录（%USERPROFILE%\.omp\agent）
_DEFAULT_AGENT_DIR = os.path.join(os.path.expanduser("~"), ".omp", "agent")

# 环境变量（与 omp 的启动脚本 OMP.ps1 一致）
_ENV_AGENT_DIR = "PI_CODING_AGENT_DIR"
_ENV_SESSION_DIR = "PI_CODING_AGENT_SESSION_DIR"

# 运行中判定：文件 mtime 新鲜度窗口（秒）
_RUNNING_FRESHNESS_S = 120

# cwd 编码目录判定
_HOME_RELATIVE_RE = re.compile(r"^-[^-].*$|^-$")       # - 或 -foo
_TMP_RELATIVE_RE = re.compile(r"^-tmp-")                # -tmp-foo
_ABS_ENCODED_RE = re.compile(r"^--.+--$")               # --encoded--


def get_agent_dir() -> str:
    """获取 omp agent 数据目录（环境变量优先，默认 ~/.omp/agent）。"""
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
    """将工作目录编码为会话目录名（与 omp 的 getDefaultSessionDirName 一致）。

    规则：
    - home 内：`-<relative>`（cwd == home 时为 `-`）
    - temp 内：`-tmp-<relative>`
    - 其他：`--<encoded-abs>--`（分隔符/冒号 → `-`）
    """
    resolved = os.path.normpath(cwd)
    home = os.path.expanduser("~")
    temp = os.environ.get("TEMP") or os.environ.get("TMP") or os.path.join(home, "AppData", "Local", "Temp")

    # 规范化用于比较（大小写/分隔符）
    norm = lambda p: os.path.normcase(os.path.abspath(p))  # noqa: E731
    r, h, t = norm(resolved), norm(home), norm(temp)

    # 保持原始路径大小写输出
    if r == h or r.startswith(h + os.sep):
        rel = os.path.relpath(resolved, home)
        if rel == ".":
            return "-"
        return "-" + rel.replace(os.sep, "-").replace(":", "-")
    if r == t or r.startswith(t + os.sep):
        rel = os.path.relpath(resolved, temp)
        return "-tmp-" + rel.replace(os.sep, "-").replace(":", "-")
    # 绝对路径编码
    stripped = re.sub(r"^[/\\]", "", resolved)
    safe = re.sub(r"[/\\:]", "-", stripped)
    return f"--{safe}--"


def _read_header(path: str) -> Optional[dict]:
    """读取 JSONL 首行 header。

    omp 文件首行可能是 title slot，第二行才是 session header；
    这里跳过 title slot 寻找 session header。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            while True:
                line = f.readline()
                if not line:
                    return None
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    return None
                if not isinstance(data, dict):
                    return None
                if data.get("type") == "session" and isinstance(data.get("id"), str):
                    return data
                if data.get("type") == "title":
                    continue  # title slot，跳过找 header
                return None
    except Exception as e:
        _log.warning("failed to read header %s: %s", path, e)
        return None


def _read_title_slot(path: str) -> str:
    """读取首行 title slot 的标题（尽力而为）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            line = f.readline()
            if not line:
                return ""
            data = json.loads(line.strip())
            if isinstance(data, dict) and data.get("type") == "title":
                return str(data.get("title", "")).strip()
    except Exception:
        pass
    return ""


def _read_first_user_message(path: str, limit_bytes: int = 64 * 1024) -> str:
    """读取首个 user 消息文本（供列表展示，尽力而为）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            buf = f.read(limit_bytes)
    except OSError:
        return ""
    for line in buf.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict) or ev.get("type") != "message":
            continue
        msg = ev.get("message") or {}
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            return " ".join(parts)
        return ""
    return ""


def _read_last_message_summary(path: str, tail_bytes: int = 32 * 1024) -> str:
    """读取最后一条消息的 role + stopReason（供状态判定，尽力而为）。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    last_role = ""
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict) or ev.get("type") != "message":
            continue
        msg = ev.get("message") or {}
        last_role = msg.get("role", "")
        break
    return last_role


def _derive_status(last_role: str) -> str:
    """由最后一条消息 role 推导会话生命周期状态。"""
    if last_role == "user":
        return "pending"
    if last_role == "assistant":
        return "complete"
    if last_role == "toolResult":
        return "interrupted"
    return "unknown"


def find_all_sessions(agent_dir: Optional[str] = None) -> List[dict]:
    """列出全部会话（跨所有 cwd 目录）。

    返回按 modified 降序排列的会话信息列表：
    { session_id, cwd, title, model, created, modified, message_count, first_message, path, status }
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
        # 跳过非会话目录（如 archive、. 开头的隐藏目录）
        if not (_HOME_RELATIVE_RE.match(name) or _TMP_RELATIVE_RE.match(name) or _ABS_ENCODED_RE.match(name)):
            # omp 目录名可能为任意相对路径形式，只要含 .jsonl 就扫描
            pass
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
                "title": _read_title_slot(path),
                "model": "",
                "created": created_ms,
                "modified": modified_ms,
                "message_count": 0,
                "first_message": _read_first_user_message(path),
                "path": path,
                "status": _derive_status(_read_last_message_summary(path)),
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
            # 前缀匹配（omp 的 --resume / --session 支持部分 ID）
            if session_id and sid.startswith(session_id):
                if best is None:
                    best = path
    return best


def list_running_sessions(agent_dir: Optional[str] = None) -> List[dict]:
    """列出可能运行中的会话。

    omp 无 pid 锁文件，判定依据：
    1. 存在 omp 进程（omp-windows.exe / omp）
    2. 会话文件 mtime 在新鲜度窗口内（被活跃写入）
    """
    if not _omp_process_running():
        return []
    window = _dt.timedelta(seconds=_RUNNING_FRESHNESS_S)
    now = _dt.datetime.now()
    sessions = find_all_sessions(agent_dir)
    running = []
    for s in sessions:
        modified = s["modified"]
        if modified <= 0:
            continue
        mtime = _dt.datetime.fromtimestamp(modified / 1000.0)
        if now - mtime <= window:
            running.append(s)
    return running


def _omp_process_running() -> bool:
    """检测是否有 omp 进程在运行（跨平台尽力而为）。"""
    try:
        if os.name == "nt":
            result = os.popen('tasklist /FI "IMAGENAME eq omp-windows.exe" /NH').read()
            if "omp-windows.exe" in result:
                return True
            result = os.popen('tasklist /FI "IMAGENAME eq omp.exe" /NH').read()
            return "omp.exe" in result
        result = os.popen("pgrep -f 'omp( |$)'").read()
        return bool(result.strip())
    except Exception:
        return False
