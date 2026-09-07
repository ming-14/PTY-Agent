"""消息历史解析适配器：从 crush.db 解析会话消息。

Crush 将数据存储在 SQLite（<project>/.crush/crush.db），核心表：

- sessions：会话元数据（id / title / tokens / cost / timestamps / todos）
- messages：每条消息（id / session_id / role / parts JSON / model / provider / finished_at）
- files / read_files：会话文件快照（版本化）

messages.parts 为 JSON 数组，每个元素形如 {"type": "<类型>", "data": {...}}，
类型全集（源码 internal/message/message.go）：

| type | data 字段 | 说明 |
|------|-----------|------|
| text | text | 正文 |
| reasoning | thinking / signature / thought_signature / started_at / finished_at | 思考 |
| image_url | url / detail | 图片引用 |
| binary | path / mime_type / data | 二进制附件 |
| tool_call | id / name / input(JSON str) / provider_executed / finished | 工具调用 |
| tool_result | tool_call_id / name / content / data / mime_type / metadata / is_error | 工具结果 |
| finish | reason / time(秒) / message / details | 结束标记 |
| shell_command | command / output / exit_code | bang 模式命令 |

roles：user / assistant / tool / system
- user 消息自动带 finish{reason:"stop"}（CreateMessage 逻辑）
- tool 消息的 parts 为 tool_result（可多个）
- assistant 消息含 text / reasoning / tool_call / finish 任意组合

解析要点：
- 时间戳为秒 int（Unix epoch），SQL 注释"毫秒"是误导
- finish part 的 reason = 消息结束原因（end_turn / tool_use / max_tokens / error ...）
- tool_call 的 input 为 JSON 字符串，需二次解析为 dict
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from . import session_locator
from ..entities import (
    Finish, Message, MessageItem, Session, ToolResult, ToolUse, Usage,
)
from ..infra.logging import get_logger

_log = get_logger("messages_db")


def _ts_to_iso(ts: int) -> str:
    """秒时间戳 → ISO 8601 字符串（本地时区）。"""
    if not ts:
        return ""
    return _dt.datetime.fromtimestamp(ts).isoformat()


def _parse_tool_input(raw: str) -> Dict[str, Any]:
    """解析工具调用的 input（JSON 字符串 → dict）。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"_raw": raw}
    except json.JSONDecodeError:
        return {"_raw": raw}


def _parse_tool_call(data: dict) -> ToolUse:
    return ToolUse(
        tool_call_id=data.get("id", ""),
        name=data.get("name", ""),
        input=_parse_tool_input(data.get("input", "")),
        provider_executed=bool(data.get("provider_executed", False)),
        finished=bool(data.get("finished", True)),
    )


def _parse_tool_result(data: dict) -> ToolResult:
    content = data.get("content") or ""
    is_error = bool(data.get("is_error", False))
    # 拒绝识别：内容含 denied 关键字（与其它 parser 一致的启发式）
    is_denied = bool(content) and any(
        kw in str(content).lower()
        for kw in ("denied by user", "rejected by user", "permission denied")
    )
    return ToolResult(
        tool_call_id=data.get("tool_call_id", ""),
        name=data.get("name", ""),
        success=not is_error and not is_denied,
        is_error=is_error,
        is_denied=is_denied,
        error=str(content) if is_error else None,
        content=content,
        data=data.get("data") or "",
        mime_type=data.get("mime_type") or "",
        metadata=data.get("metadata") or "",
    )


def _parse_shell_command(data: dict) -> MessageItem:
    """格式化 shell_command part 为文本。"""
    cmd = data.get("command", "")
    out = data.get("output", "")
    exit_code = data.get("exit_code", 0)
    text = f"$ {cmd}"
    if out:
        text += f"\n{out}"
    text += f"\n(exit code {exit_code})"
    return MessageItem(type="shell_command", text=text)


def _parse_part(wrapper: dict) -> List[MessageItem]:
    """解析单个 parts 元素为 MessageItem 列表。"""
    ptype = wrapper.get("type", "")
    data = wrapper.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    if ptype == "text":
        return [MessageItem(type="text", text=data.get("text", ""))]

    if ptype == "reasoning":
        return [MessageItem(type="thinking", text=data.get("thinking", ""))]

    if ptype == "tool_call":
        return [MessageItem(type="tool_use", tool_use=_parse_tool_call(data))]

    if ptype == "tool_result":
        return [MessageItem(type="tool_result", tool_result=_parse_tool_result(data))]

    if ptype == "finish":
        return [MessageItem(type="finish", finish=Finish(
            reason=data.get("reason", ""),
            time=data.get("time", 0),
            message=data.get("message"),
            details=data.get("details"),
        ))]

    if ptype == "shell_command":
        return [_parse_shell_command(data)]

    if ptype == "image_url":
        return [MessageItem(type="text", text=data.get("url", ""))]

    if ptype == "binary":
        return [MessageItem(type="text", text=data.get("path", "[binary attachment]"))]

    _log.warning("unknown part type: %s", ptype)
    return [MessageItem(type=ptype, text=str(data))]


def _parse_message(row: sqlite3.Row) -> Optional[Message]:
    """从 messages 表行构建 Message。"""
    parts_raw = row["parts"] or "[]"
    try:
        parts = json.loads(parts_raw)
    except (json.JSONDecodeError, TypeError):
        _log.warning("unparseable parts for message %s", row["id"])
        parts = []

    items: List[MessageItem] = []
    for wrapper in parts:
        items.extend(_parse_part(wrapper))

    ts = row["created_at"] or 0
    finished_at = row["finished_at"]

    return Message(
        id=row["id"],
        role=row["role"],
        ts=ts,
        ts_iso=_ts_to_iso(ts),
        items=items,
        model=row["model"] or None,
        provider=row["provider"] or None,
        finished_at=finished_at,
        is_summary_message=bool(row["is_summary_message"]),
    )


def load_session_messages(
    con: sqlite3.Connection, session_id: str
) -> Tuple[List[Message], Usage]:
    """从连接加载指定会话的全部消息。

    Args:
        con: crush.db 只读连接
        session_id: 会话 ID

    Returns:
        (messages, usage) — 消息按 created_at 升序
    """
    _log.info("loading messages for session %s", session_id)
    rows = con.execute(
        "SELECT * FROM messages WHERE session_id = ? "
        "AND is_summary_message = 0 ORDER BY created_at, rowid",
        (session_id,),
    ).fetchall()

    messages: List[Message] = []
    for row in rows:
        m = _parse_message(row)
        if m is not None:
            messages.append(m)

    # 会话级 usage 从 sessions 表读（messages 表无 per-message tokens）
    usage = Usage()
    srow = con.execute(
        "SELECT prompt_tokens, completion_tokens, cost FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if srow is not None:
        usage.input_tokens = srow["prompt_tokens"] or 0
        usage.output_tokens = srow["completion_tokens"] or 0
        usage.total_cost = srow["cost"] or 0.0

    _log.info("session %s: %d messages", session_id, len(messages))
    return messages, usage


def load_session_entity(con: sqlite3.Connection, session_id: str) -> Session:
    """从 sessions 表构建 Session 实体。"""
    row = con.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise KeyError(f"session not found: {session_id}")

    todos: List[Dict[str, Any]] = []
    if row["todos"]:
        try:
            todos = json.loads(row["todos"])
            if not isinstance(todos, list):
                todos = []
        except json.JSONDecodeError:
            _log.warning("unparseable todos for session %s", session_id)
            todos = []

    return Session(
        id=row["id"],
        parent_session_id=row["parent_session_id"],
        title=row["title"] or "",
        message_count=row["message_count"] or 0,
        prompt_tokens=row["prompt_tokens"] or 0,
        completion_tokens=row["completion_tokens"] or 0,
        cost=row["cost"] or 0.0,
        started_at=_ts_to_iso(row["created_at"] or 0),
        updated_at=_ts_to_iso(row["updated_at"] or 0),
        summary_message_id=row["summary_message_id"],
        todos=todos,
    )


def load_session_messages_by_id(
    session_id: str, data_dir: Optional[str] = None
) -> Tuple[Dict[str, Any], List[Message]]:
    """按会话 ID 加载消息（subagent 消息读取入口）

    Crush 的会话按项目分散在多个 crush.db 中，且命令行展示的是 hash 前缀，
    这里封装「定位 → 打开只读连接 → 加载」全过程，对外只需会话 ID 与数据目录。

    Args:
        session_id: 会话 ID（UUID / XXH3 全 hash / hash 前缀）
        data_dir: 显式数据目录，None 则遍历 projects.json 全部项目库

    Returns:
        (meta, messages) — meta 为会话元数据 dict，messages 按时间升序

    Raises:
        FileNotFoundError: 数据目录尚未建立 crush.db（agent 刚启动）
        KeyError: 会话不存在
    """
    real_id = session_locator.resolve_session_id(session_id, data_dir)
    srow = session_locator.find_session(real_id, data_dir)
    con = session_locator.open_db(os.path.join(srow["data_dir"], "crush.db"))
    try:
        session = load_session_entity(con, real_id)
        session.data_dir = srow["data_dir"]
        messages, _usage = load_session_messages(con, real_id)
    finally:
        con.close()
    _log.info("loaded session %s: %d messages", real_id, len(messages))
    return asdict(session), messages