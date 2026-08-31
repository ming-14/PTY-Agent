"""消息历史 JSONL 解析适配器。

将 <sessionId>.jsonl 解析为 (Session, List[Message])。

Claude Code 存储为每行一个 JSON 事件的追加日志，事件类型：
- user / assistant：对话消息
- mode / permission-mode / atis-latch / last-prompt：会话配置
- system：系统事件（turn_duration 等）
- attachment：附加信息（agent 列表 / token 提醒等）
- file-history-snapshot：文件快照

解析要点：
- user 消息 content 双形态：字符串（typed 输入）/ 数组（含 tool_result）
- assistant content 数组：thinking / text / tool_use
- 时间戳为 ISO 字符串 → 转毫秒 int + 保留 ISO
- usage 在 message.usage（Claude API 形态）
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from ..entities import (
    Message, MessageItem, Session, ToolResult, ToolResultEntry, ToolUse, Usage,
)
from ..infra.logging import get_logger

_log = get_logger("messages_jsonl")

# 会话配置类事件（不产生 Message）
_CONFIG_TYPES = frozenset((
    "mode", "permission-mode", "atis-latch", "last-prompt",
    "file-history-snapshot", "system", "attachment",
))

# tool_result 拒绝识别
_DENIED_RE = re.compile(r"denied by user", re.IGNORECASE)


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
    """解析 message.usage（Claude API 形态，camelCase）。"""
    if not raw:
        return None
    return Usage(
        input_tokens=raw.get("input_tokens", 0),
        output_tokens=raw.get("output_tokens", 0),
        cache_read_input_tokens=raw.get("cache_read_input_tokens", 0),
        cache_creation_input_tokens=raw.get("cache_creation_input_tokens", 0),
        total_cost=raw.get("total_cost", 0.0),
    )


def _parse_tool_use(item: dict) -> ToolUse:
    """解析 assistant tool_use content 项。"""
    return ToolUse(
        tool_call_id=item.get("id", ""),
        name=item.get("name", ""),
        input=item.get("input") or {},
    )


def _parse_tool_result_str(content: Any, name: str) -> ToolResult:
    """解析 tool_result 的 content。

    claude 的 content 为字符串；也可能为 list（多段）。
    错误/拒绝识别：
    - is_error + "denied by user" → is_denied
    - 其他错误 → error
    """
    entries: List[ToolResultEntry] = []
    result: Any = content
    error: Optional[str] = None
    is_denied = False

    if isinstance(content, str):
        lowered = content
        if _DENIED_RE.search(lowered):
            is_denied = True
            error = content if len(content) < 200 else content[:200]
    elif isinstance(content, list):
        # list 形态：可能是多个文本块，或有结构
        parts = []
        for c in content:
            if isinstance(c, dict):
                if "text" in c:
                    parts.append(str(c.get("text", "")))
                elif "query" in c and "result" in c:
                    entries.append(ToolResultEntry(
                        query=str(c.get("query", "")),
                        output=str(c.get("result", "")),
                        success=bool(c.get("success", True)),
                        error=c.get("error"),
                    ))
            else:
                parts.append(str(c))
        result = "\n".join(parts) if parts else content

    success = not is_denied and error is None

    return ToolResult(
        tool_call_id="",
        name=name,
        success=success,
        is_denied=is_denied,
        is_error=bool(error),
        error=error,
        result=result,
        entries=entries,
    )


def _parse_tool_result(item: dict, tool_names: Dict[str, str]) -> ToolResult:
    """解析 user 消息中的 tool_result content 项。

    claude 的 tool_result 只有 tool_use_id + content，不含 name；
    name 通过 tool_use_id 从已解析的 tool_use 事件回填。
    """
    tool_use_id = item.get("tool_use_id", "")
    name = tool_names.get(tool_use_id, "")
    tr = _parse_tool_result_str(item.get("content"), name)
    tr.tool_call_id = tool_use_id
    return tr


def _parse_content_item(item: dict, tool_names: Dict[str, str]) -> MessageItem:
    """解析单个 content 元素为 MessageItem（assistant / user 通用）。"""
    ctype = item.get("type", "")

    if ctype == "text":
        return MessageItem(type="text", text=item.get("text", ""))

    if ctype == "thinking":
        return MessageItem(type="thinking", text=item.get("thinking", ""))

    if ctype == "tool_use":
        return MessageItem(type="tool_use", tool_use=_parse_tool_use(item))

    if ctype == "tool_result":
        return MessageItem(type="tool_result", tool_result=_parse_tool_result(item, tool_names))

    _log.warning("unknown content type: %s", ctype)
    return MessageItem(type=ctype, text=str(item))


def _parse_user_message(event: dict, tool_names: Dict[str, str]) -> Message:
    """解析 user 事件（content 字符串或数组两种形态）。"""
    ts, ts_iso = _iso_to_ts(event.get("timestamp", ""))
    msg = event.get("message") or {}
    content = msg.get("content")

    items: List[MessageItem] = []
    if isinstance(content, str):
        items.append(MessageItem(type="text", text=content))
    elif isinstance(content, list):
        items = [_parse_content_item(c, tool_names) for c in content if isinstance(c, dict)]
    else:
        _log.warning("unexpected user content type: %s", type(content).__name__)

    return Message(
        id=event.get("uuid", ""),
        role="user",
        ts=ts,
        ts_iso=ts_iso,
        items=items,
        prompt_source=event.get("promptSource"),
    )


def _parse_assistant_message(event: dict, tool_names: Dict[str, str]) -> Message:
    """解析 assistant 事件。"""
    ts, ts_iso = _iso_to_ts(event.get("timestamp", ""))
    msg = event.get("message") or {}
    content = msg.get("content") or []
    items = [_parse_content_item(c, tool_names) for c in content if isinstance(c, dict)]

    # 记录 tool_use 的 call_id → name 映射（供 tool_result 回填）
    for item in items:
        if item.type == "tool_use" and item.tool_use:
            tool_names[item.tool_use.tool_call_id] = item.tool_use.name

    return Message(
        id=event.get("uuid", ""),
        role="assistant",
        ts=ts,
        ts_iso=ts_iso,
        items=items,
        model=msg.get("model"),
        effort=event.get("effort"),
        usage=_parse_usage(msg.get("usage")),
    )


def parse_session_meta(events: List[dict]) -> dict:
    """从事件流提取会话级元数据（mode / permission-mode / model 等）。

    Returns:
        含 mode/permission_mode/model/version/git_branch/cwd 的字典
    """
    meta: Dict[str, Any] = {}
    for ev in events:
        etype = ev.get("type", "")
        if etype == "mode":
            meta["mode"] = ev.get("mode", "")
        elif etype == "permission-mode":
            meta["permission_mode"] = ev.get("permissionMode", "")
        elif etype == "assistant":
            msg = ev.get("message") or {}
            if msg.get("model"):
                meta.setdefault("model", msg["model"])
        elif etype in ("user", "assistant"):
            meta.setdefault("version", ev.get("version", ""))
            meta.setdefault("git_branch", ev.get("gitBranch", ""))
            meta.setdefault("cwd", ev.get("cwd", ""))
            meta.setdefault("entrypoint", ev.get("entrypoint", ""))
    return meta


def _messages_from_events(events: List[dict]) -> List[Message]:
    """从事件列表构建消息列表（过滤配置类事件，回填 tool_result 名称）。"""
    messages: List[Message] = []
    tool_names: Dict[str, str] = {}  # tool_use_id → tool name（回填 tool_result）
    for ev in events:
        etype = ev.get("type", "")
        if etype == "user":
            messages.append(_parse_user_message(ev, tool_names))
        elif etype == "assistant":
            messages.append(_parse_assistant_message(ev, tool_names))
        # 其他类型（配置/系统/附加）不产生 Message
    return messages


def parse_jsonl(data: Union[str, bytes, List[dict]]) -> List[Message]:
    """从 jsonl 文本解析消息列表。

    Args:
        data: jsonl 文本内容

    Returns:
        Message 实体列表（保持原始顺序，过滤配置类事件）
    """
    events: List[dict] = []
    if isinstance(data, list):
        events = data
    else:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                _log.warning("skip unparseable jsonl line: %s", e)

    messages = _messages_from_events(events)
    _log.info("parse_jsonl: %d messages from %d events", len(messages), len(events))
    return messages


def load_jsonl(path: str) -> List[Message]:
    """从文件路径加载消息历史。"""
    _log.info("loading messages from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        return parse_jsonl(f.read())


def load_jsonl_with_meta(path: str) -> Tuple[dict, List[Message]]:
    """从文件路径加载会话元数据与消息历史（插件统一加载接口）。

    Returns:
        (meta, messages)：与 devin/opencode parser 的 msg_loader_fn
        返回约定一致（loader_meta_first=True）；一次读取避免重复解析 jsonl。
    """
    _log.info("loading messages+meta from %s", path)
    events: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                _log.warning("skip unparseable jsonl line: %s", e)
    messages = _messages_from_events(events)
    meta = parse_session_meta(events)
    _log.info("load_jsonl_with_meta: %d messages", len(messages))
    return meta, messages


def load_meta(path: str) -> dict:
    """从文件路径加载会话级元数据。"""
    _log.debug("loading session meta from %s", path)
    events: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return parse_session_meta(events)
