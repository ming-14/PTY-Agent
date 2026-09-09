"""消息历史 SQLite 解析适配器。

从 sessions.db 的 messages 表解析会话消息列表。

Goose 存储为 SQLite 表，messages 表每行一条消息：
- content_json：JSON 数组（MessageContentBlock[]）
- metadata_json：JSON（MessageMetadata）

MessageContentBlock 类型（type 标签）：
- text：普通文本
- thinking / redactedThinking：思考过程
- toolRequest：工具调用（toolCall.value.name/arguments）
- toolResponse：工具结果（toolResult.value.content/structuredContent）
- toolConfirmationRequest / actionRequired：权限/确认请求
- systemNotification / error：系统通知/错误

解析要点：
- userVisible=false + turnContext=true 的消息是 <turn-context> 系统注入，过滤
- toolRequest/toolResponse 通过 id（call_id）匹配回填工具名
- 时间戳为 Unix 秒 → 转毫秒 int + ISO 字符串
- metadata.usage 提供消息级 token 用量
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from ..entities import Message, MessageItem, ToolResult, ToolUse, Usage
from ..infra.logging import get_logger

_log = get_logger("messages_db")

# 权限拒绝识别
_DENIED_RE = re.compile(r"denied by user|rejected by user", re.IGNORECASE)


def _ts_to_iso(ts: int) -> str:
    """Unix 秒 → ISO 8601 字符串（UTC）。"""
    if not ts:
        return ""
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat()


def _parse_usage(raw: Optional[dict]) -> Optional[Usage]:
    """解析 metadata.usage（camelCase，MessageUsage 形态）。"""
    if not raw:
        return None
    return Usage(
        input_tokens=raw.get("inputTokens", 0) or 0,
        output_tokens=raw.get("outputTokens", 0) or 0,
        total_tokens=raw.get("totalTokens", 0) or 0,
        cache_read_input_tokens=raw.get("cacheReadTokens", 0) or 0,
        cache_write_input_tokens=raw.get("cacheWriteTokens", 0) or 0,
        cost=raw.get("cost", 0.0) or 0.0,
        cost_source=raw.get("costSource", ""),
        elapsed_ms=raw.get("elapsedMs"),
        time_to_first_token_ms=raw.get("timeToFirstTokenMs"),
    )


def _extract_text_content(content: Any) -> str:
    """从 toolResult.value.content 数组提取文本内容（含嵌套）。"""
    parts: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text":
                parts.append(str(node.get("text", "")))
            elif node.get("type") == "resource":
                parts.append("[resource]")
            elif node.get("type") == "image":
                parts.append("[image]")
            elif node.get("type") == "audio":
                parts.append("[audio]")
            else:
                for v in node.values():
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(content)
    return "\n".join(p for p in parts if p)


def _parse_tool_response(item: dict) -> ToolResult:
    """解析 toolResponse content 项。

    toolResult 结构：
    {
      "status": "success"|"error",
      "value": {
        "resultType": "complete",
        "content": [{"type": "text", "text": "..."}],
        "structuredContent": {"stdout": "...", "stderr": "", "exit_code": 0},
        "isError": false
      }
    }
    """
    call_id = item.get("id", "")
    raw_result = item.get("toolResult") or {}
    status = raw_result.get("status", "")
    value = raw_result.get("value") or {}
    content = value.get("content")
    structured = value.get("structuredContent") or {}
    is_error_flag = bool(value.get("isError", False))

    output = _extract_text_content(content)
    stdout = structured.get("stdout")
    stderr = structured.get("stderr")
    exit_code = structured.get("exit_code")

    # stdout/stderr 补充到 output（content 可能为空）
    if not output and stdout:
        output = stdout

    # 错误/拒绝识别
    is_denied = _DENIED_RE.search(output) is not None
    if is_denied:
        error = "tool execution denied by user"
    elif status == "error" or is_error_flag:
        error = output[:200] if output else "tool execution failed"
    elif exit_code is not None and exit_code != 0:
        error = f"command exited with code {exit_code}"
    else:
        error = None

    success = error is None and not is_denied

    return ToolResult(
        tool_call_id=call_id,
        name="",
        success=success,
        is_denied=is_denied,
        is_error=error is not None,
        error=error,
        output=output,
        exit_code=int(exit_code) if isinstance(exit_code, (int, float)) else None,
        raw=raw_result,
    )


def _parse_content_item(item: dict) -> Optional[MessageItem]:
    """解析单个 content 元素为 MessageItem。

    Returns:
        MessageItem 或 None（应过滤/无法映射的类型）
    """
    ctype = item.get("type", "")

    if ctype == "text":
        return MessageItem(type="text", text=item.get("text", ""))

    if ctype == "thinking":
        return MessageItem(type="thinking", text=item.get("thinking", ""))

    if ctype == "redactedThinking":
        return MessageItem(type="thinking", text="[redacted thinking]")

    if ctype == "toolRequest":
        tool_call = item.get("toolCall") or {}
        value = tool_call.get("value") or {}
        return MessageItem(
            type="tool_use",
            tool_use=ToolUse(
                tool_call_id=item.get("id", ""),
                name=value.get("name", ""),
                input=value.get("arguments") or {},
            ),
        )

    if ctype == "toolResponse":
        return MessageItem(type="tool_result", tool_result=_parse_tool_response(item))

    # 权限/确认/系统类：不产生用户可见消息项，但保留（标记为工具结果）
    if ctype == "toolConfirmationRequest":
        return MessageItem(
            type="tool_use",
            tool_use=ToolUse(
                tool_call_id=item.get("id", ""),
                name=item.get("toolName", ""),
                input=item.get("arguments") or {},
            ),
        )

    if ctype == "actionRequired":
        data = item.get("data") or {}
        action_type = data.get("actionType", "")
        if action_type == "toolConfirmation":
            return MessageItem(
                type="tool_use",
                tool_use=ToolUse(
                    tool_call_id=data.get("id", ""),
                    name=data.get("toolName", ""),
                    input=data.get("arguments") or {},
                ),
            )
        return None

    if ctype == "image":
        return MessageItem(type="image", text="[image]")

    if ctype == "error":
        return MessageItem(type="error", text=item.get("message", ""))

    if ctype in ("systemNotification", "frontendToolRequest"):
        return None

    _log.warning("unknown content type: %s", ctype)
    return MessageItem(type=ctype, text=str(item)[:200])


def _parse_message(row: sqlite3.Row, tool_names: Dict[str, str]) -> Message:
    """从 messages 表行解析 Message。

    Args:
        row: messages 表行（sqlite3.Row，需 dict 化）
        tool_names: call_id → tool name 映射（回填 tool_result）

    Returns:
        Message 实体
    """
    row = dict(row)
    ts = row["created_timestamp"] or 0
    ts_ms = ts * 1000

    content_json = row["content_json"] or "[]"
    try:
        content_blocks = json.loads(content_json) if isinstance(content_json, str) else content_json
    except json.JSONDecodeError:
        _log.warning("unparseable content_json for message %s", row["message_id"])
        content_blocks = []

    metadata = {}
    meta_json = row.get("metadata_json")
    if meta_json:
        try:
            metadata = json.loads(meta_json) if isinstance(meta_json, str) else meta_json
        except json.JSONDecodeError:
            _log.warning("unparseable metadata_json for message %s", row["message_id"])

    items: List[MessageItem] = []
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        item = _parse_content_item(block)
        if item is None:
            continue
        # 记录 tool_use 的 call_id → name 映射（供 tool_result 回填）
        if item.type == "tool_use" and item.tool_use:
            tool_names[item.tool_use.tool_call_id] = item.tool_use.name
        items.append(item)

    # 回填 tool_result 的工具名
    for item in items:
        if item.type == "tool_result" and item.tool_result:
            tr = item.tool_result
            if not tr.name:
                tr.name = tool_names.get(tr.tool_call_id, "")

    # 模型：metadata.inference.requestedModel
    model = None
    inference = metadata.get("inference") or {}
    if inference.get("requestedModel"):
        model = inference["requestedModel"]

    return Message(
        id=row["message_id"] or "",
        role=row["role"] or "",
        ts=ts_ms,
        ts_iso=_ts_to_iso(ts),
        items=items,
        model=model,
        user_visible=bool(metadata.get("userVisible", True)),
        turn_context=bool(metadata.get("turnContext", False)),
        usage=_parse_usage(metadata.get("usage")),
    )


def load_messages(conn: sqlite3.Connection, session_id: str) -> List[Message]:
    """从数据库加载会话的全部消息。

    Args:
        conn: 已连接的 sqlite3.Connection
        session_id: 会话 ID

    Returns:
        Message 实体列表（保持原始顺序）

    过滤规则：
        - turnContext=true 且 userVisible=false 的 <turn-context> 系统注入 → 过滤
        - 无内容项的消息 → 保留（可能是空 assistant 消息）
    """
    rows = conn.execute(
        "SELECT message_id, role, created_timestamp, content_json, metadata_json "
        "FROM messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()

    messages: List[Message] = []
    tool_names: Dict[str, str] = {}
    for row in rows:
        msg = _parse_message(row, tool_names)
        # 过滤系统注入（turn-context 且用户不可见）
        if msg.turn_context and not msg.user_visible:
            continue
        # 过滤 agent-only 隐藏消息（userVisible=false 且非 turn_context）
        if not msg.user_visible:
            continue
        messages.append(msg)

    _log.info("load_messages: %d messages for session %s", len(messages), session_id)
    return messages


def load_usage_ledger(conn: sqlite3.Connection, session_id: str) -> List[dict]:
    """加载会话的 usage_ledger 明细。"""
    rows = conn.execute(
        "SELECT * FROM usage_ledger WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]