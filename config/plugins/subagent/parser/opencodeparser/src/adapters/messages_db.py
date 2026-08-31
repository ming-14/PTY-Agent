"""消息历史解析适配器：从 opencode.db 解析会话消息。

opencode（sst/opencode）将数据存储在 SQLite（~/.local/share/opencode/opencode.db），
核心表为 session / message / part / event（event sourcing + 物化视图）：

- session：会话元数据（id / slug / directory / title / cost / tokens / model / agent）
- message：每条消息（id / session_id / time_created / data JSON）
- part：消息内容项（id / message_id / session_id / time_created / data JSON）

message.data 关键字段：
- role：user / assistant
- parentID：父消息 ID（消息树）
- tokens：{input, output, reasoning, cache:{read, write}}
- cost / modelID / providerID / variant / mode / agent / finish / path

part.data.type 取值：
- user 消息：text（用户输入）/ file（图片等附件）
- assistant 消息：text（回复正文）/ reasoning（思考过程）/
  tool（工具调用与结果，state 字段同时承载调用与结果）/
  step-start / step-finish（生命周期事件，过滤）

tool part 的 state 字段：
- status：completed / error / running
- input：工具调用参数
- output：工具输出（completed 时）
- error：错误信息（error 时）

解析要点：
- 时间戳为毫秒 int（Unix epoch ms）
- step-start / step-finish 为内部生命周期事件，不产生 MessageItem
- tool part：status=running 视为 tool_use（调用），completed/error 视为 tool_result
- usage 从 message.data.tokens 聚合
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from ..entities import (
    Message, MessageItem, Session, ToolResult, ToolUse, Usage,
)
from ..infra.logging import get_logger

_log = get_logger("messages_db")

# 过滤的生命周期事件（不产生 MessageItem）
_SKIP_PART_TYPES = frozenset(("step-start", "step-finish", "compaction"))


def _ts_to_iso(ts: int) -> str:
    """毫秒时间戳 → ISO 8601 字符串（UTC）。"""
    if not ts:
        return ""
    return _dt.datetime.fromtimestamp(ts / 1000.0, tz=_dt.timezone.utc).isoformat()


def _parse_usage(raw: Optional[dict]) -> Optional[Usage]:
    """解析 message.data.tokens（opencode 形态）。"""
    if not raw:
        return None
    cache = raw.get("cache") or {}
    return Usage(
        input_tokens=raw.get("input", 0),
        output_tokens=raw.get("output", 0),
        reasoning_tokens=raw.get("reasoning", 0),
        cache_read_input_tokens=cache.get("read", 0),
        cache_write_input_tokens=cache.get("write", 0),
        total_cost=0.0,
    )


def _parse_tool_use(tool_part: dict) -> ToolUse:
    """解析 tool part 为 ToolUse（调用形态）。"""
    state = tool_part.get("state") or {}
    return ToolUse(
        tool_call_id=tool_part.get("callID", ""),
        name=tool_part.get("tool", ""),
        input=state.get("input") or {},
    )


def _parse_tool_result(tool_part: dict) -> ToolResult:
    """解析 tool part 为 ToolResult（结果形态）。

    opencode 的 tool part 调用与结果同体：
    - status=completed：成功，output 为工具输出
    - status=error：失败，error 为错误信息
    - 拒绝：error 含 "rejected by user" / "denied by user" / "dismissed"
    """
    state = tool_part.get("state") or {}
    status = state.get("status", "")
    error = state.get("error")
    is_error = status == "error"
    is_denied = bool(error) and any(
        kw in str(error) for kw in ("denied by user", "rejected by user", "dismissed")
    )

    return ToolResult(
        tool_call_id=tool_part.get("callID", ""),
        name=tool_part.get("tool", ""),
        success=not is_error and not is_denied,
        is_denied=is_denied,
        is_error=is_error,
        error=str(error) if error else None,
        output=state.get("output"),
        raw=tool_part,
    )


def _parse_part(part: dict) -> List[MessageItem]:
    """解析单个 part 为 MessageItem 列表；生命周期事件返回空列表。

    tool part 同时承载调用与结果（state 字段），
    完成时同时输出 tool_use（调用）+ tool_result（结果）。
    """
    ptype = part.get("type", "")
    if ptype in _SKIP_PART_TYPES:
        return []

    if ptype == "text":
        return [MessageItem(type="text", text=part.get("text", ""))]

    if ptype == "reasoning":
        return [MessageItem(type="thinking", text=part.get("text", ""))]

    if ptype == "tool":
        state = part.get("state") or {}
        status = state.get("status", "")
        items: List[MessageItem] = []
        # 工具调用形态（只要有 input 或 callID 就输出）
        if state.get("input") or part.get("callID"):
            items.append(MessageItem(type="tool_use", tool_use=_parse_tool_use(part)))
        # 结果形态（completed/error 有 output/error 时输出）
        if status in ("completed", "error"):
            items.append(MessageItem(type="tool_result", tool_result=_parse_tool_result(part)))
        return items

    if ptype == "file":
        return [MessageItem(type="text", text=part.get("filename") or part.get("mime") or "")]

    if ptype == "patch":
        # 文件补丁内容（opencode 新版 part 类型，展示为文本）
        return [MessageItem(type="text", text=part.get("text") or "")]

    _log.debug("unknown part type: %s", ptype)
    return [MessageItem(type=ptype, text=str(part))]


def _parse_message(row_data: str, parts: List[dict]) -> Optional[Message]:
    """从 message.data JSON + parts 列表构建 Message。"""
    try:
        d = json.loads(row_data)
    except (json.JSONDecodeError, TypeError):
        _log.warning("unparseable message data: %r", row_data[:200])
        return None

    role = d.get("role", "")
    ts = (d.get("time") or {}).get("created", 0) if isinstance(d.get("time"), dict) else 0

    items: List[MessageItem] = []
    for p in parts:
        items.extend(_parse_part(p))

    # 空消息（无有效 part）不输出
    if not items:
        return None

    model_info = d.get("model") or {}
    msg = Message(
        id="",
        role=role,
        ts=ts,
        ts_iso=_ts_to_iso(ts),
        items=items,
        model=d.get("modelID") or (model_info.get("modelID") if isinstance(model_info, dict) else None),
        provider=d.get("providerID"),
        agent=d.get("agent"),
        finish=d.get("finish"),
        usage=_parse_usage(d.get("tokens")),
        parent_id=d.get("parentID"),
    )
    return msg


def parse_session_messages(
    messages_rows: List[Tuple[str, str]],
    parts_rows: List[Tuple[str, str, str]],
) -> List[Message]:
    """从 message/part 行解析消息列表。

    Args:
        messages_rows: [(message_id, message_data_json), ...]
        parts_rows: [(part_id, message_id, part_data_json), ...]

    Returns:
        Message 实体列表（按消息创建时间顺序）
    """
    # 按 message_id 聚合 parts（保持 part 创建顺序）
    parts_by_msg: Dict[str, List[dict]] = {}
    for _, msg_id, part_data in parts_rows:
        try:
            pd = json.loads(part_data)
        except (json.JSONDecodeError, TypeError):
            continue
        parts_by_msg.setdefault(msg_id, []).append(pd)

    messages: List[Message] = []
    for msg_id, msg_data in messages_rows:
        parts = parts_by_msg.get(msg_id, [])
        m = _parse_message(msg_data, parts)
        if m is not None:
            m.id = msg_id
            messages.append(m)
    return messages


def _aggregate_usage(messages: List[Message]) -> Usage:
    """累加所有消息的 usage 为会话级。"""
    usage = Usage()
    for m in messages:
        if m.usage:
            usage.input_tokens += m.usage.input_tokens
            usage.output_tokens += m.usage.output_tokens
            usage.reasoning_tokens += m.usage.reasoning_tokens
            usage.cache_read_input_tokens += m.usage.cache_read_input_tokens
            usage.cache_write_input_tokens += m.usage.cache_write_input_tokens
            usage.total_cost += m.usage.total_cost
    return usage


def load_session_messages(
    con: sqlite3.Connection, session_id: str
) -> Tuple[List[Message], Usage]:
    """从连接加载指定会话的全部消息。

    Args:
        con: opencode.db 只读连接
        session_id: 会话 ID（如 ses_xxx）

    Returns:
        (messages, usage)
    """
    _log.info("loading messages for session %s", session_id)

    msg_rows = con.execute(
        "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created, id",
        (session_id,),
    ).fetchall()
    msg_id_list = [r[0] for r in msg_rows]
    parts_rows: List[Tuple[str, str, str]] = []
    if msg_id_list:
        # 分批查询避免 SQL 参数上限
        for i in range(0, len(msg_id_list), 500):
            chunk = msg_id_list[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            parts_rows.extend(con.execute(
                f"SELECT id, message_id, data FROM part "
                f"WHERE message_id IN ({placeholders}) ORDER BY time_created, id",
                chunk,
            ).fetchall())

    messages = parse_session_messages(msg_rows, parts_rows)
    usage = _aggregate_usage(messages)
    _log.info("session %s: %d messages", session_id, len(messages))
    return messages, usage


def load_session_messages_by_id(session_id: str, data_dir: Optional[str] = None) -> Tuple[dict, List[Message]]:
    """按会话 ID 加载消息（便捷函数，自动打开/关闭数据库）。

    用于子代理插件 _recent_messages_by_uid 的直通路径：
    uid = session_id（opencode interactive 发现后）或 title（oneshot 定位后），
    本函数内部打开 opencode.db 查询。

    Returns:
        (meta dict, Message 实体列表)
        meta 包含 session_id / title / usage 等字段
    """
    from . import session_locator
    con = session_locator.open_db(data_dir)
    try:
        messages, usage = load_session_messages(con, session_id)
        meta = {
            "id": session_id,
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
            },
        }
        _log.info("load_session_messages_by_id: %s -> %d messages", session_id, len(messages))
        return meta, messages
    finally:
        con.close()