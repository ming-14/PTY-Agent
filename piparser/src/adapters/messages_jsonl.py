"""消息历史 JSONL 解析适配器。

将 <timestamp>_<uuid>.jsonl 解析为 (Session, List[Message])。

Pi 存储为每行一个 JSON 事件的追加日志（树结构，id/parentId 链接），
事件类型：
- session：会话 header（首行，非树节点）
- message：对话消息（user / assistant / toolResult / bashExecution / custom / branchSummary / compactionSummary）
- model_change / thinking_level_change：会话配置
- compaction / branch_summary：上下文压缩与分支摘要
- custom / custom_message：扩展状态与扩展消息
- label / session_info：书签与会话名

解析要点：
- 时间戳双形态：entry 层 ISO 字符串 → 毫秒 int + 保留 ISO；
  message 内部 timestamp 为毫秒 int（优先使用）
- assistant content 数组：thinking / text / toolCall
- toolResult 为独立消息（role=toolResult，含 toolCallId/toolName）
- toolCall.arguments 是对象（非 JSON 字符串）
- usage 在 message.usage（pi 形态：input/output/cacheRead/cacheWrite/reasoning/totalTokens/cost）
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Dict, List, Optional, Tuple

from ..entities import Message, MessageItem, Session, ToolResult, ToolUse, Usage
from ..infra.logging import get_logger

_log = get_logger("messages_jsonl")

# 会话配置类 entry（不产生 Message）
_NON_MESSAGE_TYPES = frozenset((
    "model_change", "thinking_level_change", "custom", "label",
))

# 树节点基础字段（所有 entry 除 header 外都有）
_ENTRY_BASE_FIELDS = ("type", "id", "parentId", "timestamp")


def _iso_to_ts(iso: str) -> Tuple[int, str]:
    """ISO 8601 时间戳 → (毫秒 int, 原样 ISO 字符串)。

    无法解析时返回 (0, 原字符串)。
    """
    if not iso:
        return 0, iso
    try:
        dt = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        ts = int(dt.timestamp() * 1000)
        return ts, iso
    except (ValueError, TypeError):
        _log.warning("unparseable timestamp: %r", iso)
        return 0, iso


def _parse_usage(raw: Optional[dict]) -> Optional[Usage]:
    """解析 message.usage（pi 形态，camelCase）。"""
    if not raw:
        return None
    cost = raw.get("cost") or {}
    return Usage(
        input_tokens=raw.get("input", 0),
        output_tokens=raw.get("output", 0),
        cache_read_tokens=raw.get("cacheRead", 0),
        cache_write_tokens=raw.get("cacheWrite", 0),
        reasoning_tokens=raw.get("reasoning", 0),
        total_tokens=raw.get("totalTokens", 0),
        cost=cost.get("total", 0.0),
    )


def _parse_text_content(content: Any) -> str:
    """将 message.content 提取为纯文本。

    content 可能是字符串（user 消息）或 content 块数组。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            t = block.get("type")
            if t == "text":
                parts.append(str(block.get("text", "")))
            elif t == "thinking":
                parts.append(str(block.get("thinking", "")))
            elif t == "image":
                parts.append("[image]")
        return "\n".join(parts)
    return ""


def _parse_tool_use(block: dict) -> ToolUse:
    """解析 assistant content 的 toolCall 块。"""
    return ToolUse(
        tool_call_id=block.get("id", ""),
        name=block.get("name", ""),
        input=block.get("arguments") or {},
    )


def _parse_tool_result(msg: dict) -> ToolResult:
    """解析 role=toolResult 消息为 ToolResult。"""
    content = msg.get("content")
    output = _parse_text_content(content)
    is_error = bool(msg.get("isError", False))
    # 拒绝识别（Pi 无明确 denied 字段，错误文本兜底）
    lowered = output.lower()
    is_denied = not is_error and ("denied by user" in lowered or "rejected by user" in lowered)
    if is_denied:
        is_error = False
    error = output[:200] if (is_error or is_denied) and output else None

    return ToolResult(
        tool_call_id=msg.get("toolCallId", ""),
        name=msg.get("toolName", ""),
        success=not is_error and not is_denied,
        is_denied=is_denied,
        is_error=is_error,
        error=error,
        output=output,
        raw=msg,
    )


def _parse_bash_execution(msg: dict) -> List[MessageItem]:
    """解析 role=bashExecution 消息（! 前缀 bash 模式）为 tool_result 项。

    bashExecution 消息：command / output / exitCode / cancelled / truncated。
    """
    exit_code = msg.get("exitCode")
    output = msg.get("output", "")
    cancelled = bool(msg.get("cancelled", False))
    is_error = exit_code not in (None, 0) or cancelled

    return [MessageItem(
        type="tool_result",
        tool_result=ToolResult(
            tool_call_id="",
            name="bash",
            success=not is_error,
            is_denied=False,
            is_error=is_error,
            error=output[:200] if is_error and output else None,
            output=output,
            raw=msg,
        ),
    )]


def _role_to_message(msg: dict, entry_id: str, entry_ts: int, entry_ts_iso: str) -> Optional[Message]:
    """将 message entry 的 message 字段转为 Message。

    返回 None 表示该消息无需输出（如无内容的系统消息）。
    """
    role = msg.get("role", "")
    ts = msg.get("timestamp")
    ts_iso = entry_ts_iso
    if isinstance(ts, int) and ts > 0:
        ts_iso = _dt.datetime.fromtimestamp(ts / 1000.0, tz=_dt.timezone.utc).isoformat()
    else:
        ts = entry_ts

    items: List[MessageItem] = []

    if role == "user":
        content = msg.get("content")
        if isinstance(content, str):
            if content.startswith("<system-reminder>"):
                return None
            items.append(MessageItem(type="text", text=content))
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                t = block.get("type")
                if t == "text":
                    items.append(MessageItem(type="text", text=str(block.get("text", ""))))
                elif t == "image":
                    items.append(MessageItem(
                        type="text",
                        text=f"[image: {block.get('mimeType', 'unknown')}]",
                    ))
                # thinking/toolCall 不应出现在 user 消息，忽略

    elif role == "assistant":
        content = msg.get("content") or []
        for block in content:
            if not isinstance(block, dict):
                continue
            t = block.get("type")
            if t == "text":
                items.append(MessageItem(type="text", text=str(block.get("text", ""))))
            elif t == "thinking":
                items.append(MessageItem(type="thinking", text=str(block.get("thinking", ""))))
            elif t == "toolCall":
                items.append(MessageItem(type="tool_use", tool_use=_parse_tool_use(block)))
            elif t == "image":
                items.append(MessageItem(
                    type="text",
                    text=f"[image: {block.get('mimeType', 'unknown')}]",
                ))

    elif role == "toolResult":
        items.append(MessageItem(type="tool_result", tool_result=_parse_tool_result(msg)))

    elif role == "bashExecution":
        items = _parse_bash_execution(msg)

    elif role == "custom":
        # 扩展注入消息（LLM 可见）
        text = _parse_text_content(msg.get("content"))
        if text:
            items.append(MessageItem(type="text", text=text))
        if not items:
            return None

    elif role == "branchSummary":
        text = msg.get("summary", "")
        if text:
            items.append(MessageItem(type="text", text=text))

    elif role == "compactionSummary":
        text = msg.get("summary", "")
        if text:
            items.append(MessageItem(type="text", text=text))

    else:
        _log.warning("unknown role: %s", role)
        return None

    if not items:
        return None

    return Message(
        id=entry_id,
        role=role,
        ts=ts,
        ts_iso=ts_iso,
        items=items,
        model=msg.get("model"),
        provider=msg.get("provider"),
        api=msg.get("api"),
        stop_reason=msg.get("stopReason"),
        response_id=msg.get("responseId"),
        usage=_parse_usage(msg.get("usage")),
    )


def load_session_file(file_path: str) -> Tuple[Session, List[Message]]:
    """解析会话 JSONL 文件。

    Args:
        file_path: 会话文件路径

    Returns:
        (Session, List[Message])
    """
    entries: List[dict] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                _log.warning("skipping malformed line %d in %s", line_no, file_path)

    if not entries:
        raise ValueError(f"empty session file: {file_path}")

    header = entries[0]
    if header.get("type") != "session" or not isinstance(header.get("id"), str):
        raise ValueError(f"invalid session header in {file_path}")

    # ── 会话元数据 ──
    session = Session(
        id=header.get("id", ""),
        cwd=header.get("cwd", ""),
        started_at=header.get("timestamp", ""),
        parent_session=header.get("parentSession", ""),
        source=file_path,
        usage=Usage(),
    )

    messages: List[Message] = []

    # 扫描全部 entry：聚合会话元数据 + 收集消息
    for entry in entries[1:]:
        if not isinstance(entry, dict):
            continue
        etype = entry.get("type", "")
        entry_id = entry.get("id", "")
        entry_ts, entry_ts_iso = _iso_to_ts(entry.get("timestamp", ""))

        if etype == "message":
            msg = entry.get("message")
            if not isinstance(msg, dict):
                continue
            m = _role_to_message(msg, entry_id, entry_ts, entry_ts_iso)
            if m is not None:
                messages.append(m)

        elif etype == "model_change":
            provider = entry.get("provider")
            model_id = entry.get("modelId")
            if provider:
                session.model_provider = provider
            if model_id:
                session.model = model_id

        elif etype == "thinking_level_change":
            level = entry.get("thinkingLevel")
            if level:
                session.thinking_level = level

        elif etype == "session_info":
            name = entry.get("name")
            if name is not None:
                session.title = str(name).strip()

        elif etype == "compaction":
            # 压缩摘要：作为 compactionSummary 消息（LLM 可见）
            summary = entry.get("summary")
            usage = _parse_usage(entry.get("usage"))
            if summary:
                m = Message(
                    id=entry_id,
                    role="compactionSummary",
                    ts=entry_ts,
                    ts_iso=entry_ts_iso,
                    items=[MessageItem(type="text", text=str(summary))],
                    usage=usage,
                )
                messages.append(m)
            if usage:
                _add_usage(session, usage)

        elif etype == "branch_summary":
            summary = entry.get("summary")
            usage = _parse_usage(entry.get("usage"))
            if summary:
                m = Message(
                    id=entry_id,
                    role="branchSummary",
                    ts=entry_ts,
                    ts_iso=entry_ts_iso,
                    items=[MessageItem(type="text", text=str(summary))],
                    usage=usage,
                )
                messages.append(m)
            if usage:
                _add_usage(session, usage)

        elif etype == "custom_message":
            # 扩展注入消息（LLM 可见，TUI 可显示）
            text = _parse_text_content(entry.get("content"))
            if text:
                m = Message(
                    id=entry_id,
                    role="custom",
                    ts=entry_ts,
                    ts_iso=entry_ts_iso,
                    items=[MessageItem(type="text", text=text)],
                )
                messages.append(m)

        elif etype in _NON_MESSAGE_TYPES:
            # custom / label：不入上下文，忽略
            continue

        else:
            _log.debug("skipping entry type: %s", etype)

    # ── 会话级 usage 聚合 ──
    for m in messages:
        if m.usage:
            _add_usage(session, m.usage)

    return session, messages


def _add_usage(session: Session, usage: Usage) -> None:
    """将单条 usage 累加到会话级 usage。"""
    u = session.usage
    u.input_tokens += usage.input_tokens
    u.output_tokens += usage.output_tokens
    u.cache_read_tokens += usage.cache_read_tokens
    u.cache_write_tokens += usage.cache_write_tokens
    u.reasoning_tokens += usage.reasoning_tokens
    u.total_tokens += usage.total_tokens
    u.cost += usage.cost
