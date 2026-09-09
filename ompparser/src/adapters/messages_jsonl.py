"""消息历史 JSONL 解析适配器。

将 <timestamp>_<uuid>.jsonl 解析为 (Session, List[Message])。

omp（oh-my-pi）存储为每行一个 JSON 事件的追加日志（树结构，id/parentId 链接），
事件类型：
- title：首行固定宽度 title slot（可变当前会话标题，非树节点）
- session：会话 header（第二行，非树节点）
- message：对话消息（user / assistant / toolResult）
- model_change / thinking_level_change：会话配置
- title_change：标题变更审计（追加式）
- compaction / branch_summary：上下文压缩与分支摘要
- custom / custom_message：扩展状态与扩展消息
- label / ttsr_injection / session_init / mode_change / credential_pin /
  service_tier_change / reset_boundary：其他扩展条目

解析要点：
- 时间戳双形态：entry 层 ISO 字符串 → 毫秒 int + 保留 ISO；
  message 内部 timestamp 为毫秒 int（优先使用）
- assistant content 数组：thinking / text / toolCall / image
- toolResult 为独立消息（role=toolResult，含 toolCallId/toolName/isError/details）
- toolCall.arguments 是对象（非 JSON 字符串），partialArgs 为流式原始 JSON 字符串
- usage 在 message.usage（omp 形态：input/output/cacheRead/cacheWrite/totalTokens/cost）
- model_change 的 model 为 "provider/modelId" 组合格式（与 pi 的 provider+modelId 不同）
- title：首行 title slot 提供当前标题；title_change 追加式记录变更历史
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
    "service_tier_change", "ttsr_injection", "session_init", "mode_change",
    "credential_pin", "reset_boundary", "title",
))


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
    """解析 message.usage（omp 形态，camelCase）。"""
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
    """解析 role=toolResult 消息为 ToolResult。

    omp 的 toolResult：content 数组 + toolCallId/toolName/isError + details。
    """
    content = msg.get("content")
    output = _parse_text_content(content)
    is_error = bool(msg.get("isError", False))
    # 拒绝识别（omp 无明确 denied 字段，错误文本兜底）
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

    Raises:
        ValueError: 文件为空或 header 无效
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

    # ── 首行 title slot（固定宽度可变槽位，非树节点）──
    title = ""
    title_updated_at = ""
    first = entries[0]
    if isinstance(first, dict) and first.get("type") == "title":
        title = str(first.get("title", "")).strip()
        title_updated_at = str(first.get("updatedAt", ""))
        entries = entries[1:]
    if not entries:
        raise ValueError(f"session file has no header: {file_path}")

    # ── session header（第二行）──
    header = entries[0]
    if header.get("type") != "session" or not isinstance(header.get("id"), str):
        raise ValueError(f"invalid session header in {file_path}")

    session = Session(
        id=header.get("id", ""),
        cwd=header.get("cwd", ""),
        started_at=header.get("timestamp", ""),
        title=title,
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
            # omp 的 model 为 "provider/modelId" 组合格式
            model = entry.get("model")
            if isinstance(model, str) and model:
                session.model = model
                if "/" in model:
                    provider, _, model_id = model.partition("/")
                    session.model_provider = provider
                    session.model = model_id
                else:
                    session.model_provider = ""

        elif etype == "thinking_level_change":
            level = entry.get("thinkingLevel")
            if level:
                session.thinking_level = str(level)

        elif etype == "title_change":
            new_title = entry.get("title")
            if isinstance(new_title, str) and new_title:
                session.title = new_title.strip()

        elif etype == "compaction":
            # 压缩摘要（omp 的 compaction 不直接作为消息；summary 记录压缩边界）
            summary = entry.get("summary")
            usage = _parse_usage(entry.get("usage"))
            if summary and isinstance(summary, str):
                m = Message(
                    id=entry_id,
                    role="compactionSummary",
                    ts=entry_ts,
                    ts_iso=entry_ts_iso,
                    items=[MessageItem(type="text", text=summary)],
                    usage=usage,
                )
                messages.append(m)
            if usage:
                _add_usage(session, usage)

        elif etype == "branch_summary":
            summary = entry.get("summary")
            usage = _parse_usage(entry.get("usage"))
            if summary and isinstance(summary, str):
                m = Message(
                    id=entry_id,
                    role="branchSummary",
                    ts=entry_ts,
                    ts_iso=entry_ts_iso,
                    items=[MessageItem(type="text", text=summary)],
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
            # custom / label / 配置类：不入上下文，忽略
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
