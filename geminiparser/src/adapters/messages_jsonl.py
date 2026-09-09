"""消息历史 JSONL 解析适配器。

将 session-<ts>-<uuid8>.jsonl 解析为 (meta dict, List[Message])。

Gemini CLI 存储为每行一个 JSON 事件的追加日志，事件类型：
- 首行：会话元数据 {sessionId, projectHash, startTime, lastUpdated, kind}
- $set 状态更新：{lastUpdated, messages(完整快照), memoryScratchpad, summary}
- 消息事件：
  - user：content[{text}] 或 content[{functionResponse}]（工具结果回传）
  - gemini：content 为字符串，thoughts[] 思考分片，toolCalls[] 工具调用，tokens, model
  - info：提示事件（Request cancelled / token 超限 / Response truncated）
  - error：API 错误

解析要点：
- 同一消息 id 流式期间先写纯文本版，完成后补 toolCalls 重写 → 按 id 去重保留最后
- toolCalls 结果与后续 user 消息 functionResponse 双写 → 优先用 toolCalls 内嵌结果
- 系统注入的初始 user 消息（<session_context>）过滤，不列入消息列表
- $set.messages 是物化快照（含 session_context），仅作兜底/校验，不直接作为消息源
- 时间戳为 ISO 字符串 → 转毫秒 int + 保留 ISO
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Dict, List, Optional, Tuple

from ..entities import Message, MessageItem, ToolResult, ToolUse, Usage
from ..infra.logging import get_logger

_log = get_logger("messages_jsonl")

# 系统注入消息标记（会话开头，非真实用户输入）
_SYSTEM_INJECT_MARKS = ("<session_context>",)

# 消息事件类型（$set 事件除外）
_MESSAGE_TYPES = ("user", "gemini", "info", "error")

# 内部生命周期事件类型（不产生消息）
_SKIP_TYPES = frozenset(("info", "error"))


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


def _is_system_injected(text: str) -> bool:
    """判断是否为会话开头的系统注入消息。"""
    return any(mark in text for mark in _SYSTEM_INJECT_MARKS)


def _parse_usage(raw: Optional[dict]) -> Optional[Usage]:
    """解析消息的 tokens 字段（gemini 形态）。

    tokens: {input, output, cached, thoughts, tool, total}
    多数为 null → 返回 None。
    """
    if not raw:
        return None
    return Usage(
        input_tokens=raw.get("input", 0) or 0,
        output_tokens=raw.get("output", 0) or 0,
        cached_tokens=raw.get("cached", 0) or 0,
        thoughts_tokens=raw.get("thoughts", 0) or 0,
        tool_tokens=raw.get("tool", 0) or 0,
        total_tokens=raw.get("total", 0) or 0,
    )


def _parse_tool_use(tc: dict) -> ToolUse:
    """解析 toolCalls 条目为 ToolUse（调用形态）。"""
    return ToolUse(
        tool_call_id=tc.get("id", ""),
        name=tc.get("name", ""),
        input=tc.get("args") or {},
    )


def _parse_tool_result(tc: dict) -> ToolResult:
    """解析 toolCalls 条目为 ToolResult（结果形态）。

    status：success / error / cancelled
    result[] 内嵌 functionResponse：response.output / response.error
    """
    status = tc.get("status", "")
    is_error = status == "error"
    is_denied = status == "cancelled" or (
        status == "error" and any(
            kw in str(tc.get("resultDisplay", ""))
            for kw in ("cancelled", "rejected", "denied")
        )
    )
    error: Optional[str] = None
    output: Any = None

    # 从 result[] 提取 response
    for r in tc.get("result") or []:
        fr = r.get("functionResponse") or {}
        resp = fr.get("response") or {}
        if resp.get("error"):
            error = str(resp["error"])
            is_error = True
        if "output" in resp:
            output = resp.get("output")

    # resultDisplay 文本形态兜底
    rd = tc.get("resultDisplay")
    if error is None and isinstance(rd, str) and (
        rd.startswith("Error:") or "error" in rd.lower()
    ):
        error = rd
        is_error = True
    if is_denied and not error:
        error = "operation cancelled or rejected by user"

    return ToolResult(
        tool_call_id=tc.get("id", ""),
        name=tc.get("name", ""),
        success=not is_error and not is_denied,
        is_denied=is_denied,
        is_error=is_error,
        error=error,
        output=output,
        raw=tc,
    )


def _parse_gemini_message(ev: dict) -> Message:
    """解析 gemini 消息事件 → Message。

    内容项按序聚合：
    1. thoughts[] → thinking（若有）
    2. content 字符串 → text（若有）
    3. toolCalls[] → tool_use（调用）+ tool_result（结果）配对
    """
    ts, ts_iso = _iso_to_ts(ev.get("timestamp", ""))
    items: List[MessageItem] = []

    # 1. 思考过程（分片描述拼接）
    thoughts = ev.get("thoughts") or []
    thinking_parts = [t.get("description", "") for t in thoughts if t.get("description")]
    if thinking_parts:
        items.append(MessageItem(type="thinking", text="".join(thinking_parts)))

    # 2. 回复文本
    content = ev.get("content")
    if isinstance(content, str) and content.strip():
        items.append(MessageItem(type="text", text=content))

    # 3. 工具调用（调用 + 结果同体）
    for tc in ev.get("toolCalls") or []:
        items.append(MessageItem(type="tool_use", tool_use=_parse_tool_use(tc)))
        items.append(MessageItem(type="tool_result", tool_result=_parse_tool_result(tc)))

    return Message(
        id=ev.get("id", ""),
        role="assistant",
        ts=ts,
        ts_iso=ts_iso,
        items=items,
        model=ev.get("model"),
        usage=_parse_usage(ev.get("tokens")),
    )


def _parse_user_message(ev: dict) -> Message:
    """解析 user 消息事件 → Message。

    content[] 元素：
    - {text}：用户输入（含 @file 展开的多 text 项）
    - {functionResponse}：工具结果回传（与 gemini toolCalls 双写）
    """
    ts, ts_iso = _iso_to_ts(ev.get("timestamp", ""))
    items: List[MessageItem] = []
    text_parts: List[str] = []

    for c in ev.get("content") or []:
        if "text" in c and c.get("text"):
            text_parts.append(c["text"])
        elif "functionResponse" in c:
            fr = c["functionResponse"] or {}
            resp = fr.get("response") or {}
            error = resp.get("error")
            is_error = bool(error)
            is_denied = bool(error) and any(
                kw in str(error) for kw in ("rejected", "denied", "cancelled")
            )
            items.append(MessageItem(
                type="tool_result",
                tool_result=ToolResult(
                    tool_call_id=fr.get("id", ""),
                    name=fr.get("name", ""),
                    success=not is_error and not is_denied,
                    is_denied=is_denied,
                    is_error=is_error,
                    error=str(error) if error else None,
                    output=resp.get("output"),
                    raw=c,
                ),
            ))

    # 用户文本合并为一条 text（@file 引用的多个 text 项拼接）
    user_text = "\n".join(t for t in text_parts if t)
    if user_text:
        items.insert(0, MessageItem(type="text", text=user_text))

    return Message(
        id=ev.get("id", ""),
        role="user",
        ts=ts,
        ts_iso=ts_iso,
        items=items,
        model=ev.get("model"),
        usage=_parse_usage(ev.get("tokens")),
    )


def _parse_events(events: List[dict]) -> Tuple[Dict[str, Any], List[Message]]:
    """解析事件列表 → (会话元数据 dict, Message 实体列表)。

    处理：
    - 首行 sessionId 元数据
    - $set 状态更新（messages 快照仅统计，不直接展开为消息源）
    - 消息事件按 id 去重（保留最后出现）
    - 系统注入消息过滤
    """
    meta: Dict[str, Any] = {}
    by_id: Dict[str, Message] = {}
    had_error = False
    had_cancelled = False

    for ev in events:
        # 首行会话元数据
        if "sessionId" in ev:
            meta["sessionId"] = ev["sessionId"]
            meta["projectHash"] = ev.get("projectHash", "")
            meta["startTime"] = ev.get("startTime", "")
            meta["kind"] = ev.get("kind", "")
            continue

        # $set 状态更新
        if "$set" in ev:
            s = ev["$set"]
            if "summary" in s:
                meta["summary"] = s["summary"]
            # messages 快照：记录最后快照消息数（校验用），不作为消息源
            if "messages" in s:
                meta.setdefault("snapshot_sizes", []).append(len(s["messages"]))
            continue

        # 消息事件
        etype = ev.get("type", "")
        if etype not in _MESSAGE_TYPES:
            continue

        if etype == "info":
            content = ev.get("content", "")
            if "cancelled" in str(content).lower():
                had_cancelled = True
            continue
        if etype == "error":
            had_error = True
            continue
        if etype == "gemini":
            msg = _parse_gemini_message(ev)
        else:  # user
            # 系统注入消息过滤
            raw_text = "".join(
                c.get("text", "") for c in ev.get("content") or []
                if "text" in c
            )
            if _is_system_injected(raw_text):
                continue
            msg = _parse_user_message(ev)

        if not msg.id:
            continue
        # 同 id 去重：保留最后出现
        by_id[msg.id] = msg

    if had_error:
        meta["status"] = "error"
    elif had_cancelled:
        meta["status"] = "cancelled"
    else:
        meta["status"] = "ended"

    messages = list(by_id.values())
    _log.info("parse_events: %d messages from %d events", len(messages), len(events))
    return meta, messages


def parse_jsonl(data: str) -> Tuple[Dict[str, Any], List[Message]]:
    """从 jsonl 文本解析会话元数据与消息列表。

    Args:
        data: jsonl 文本内容

    Returns:
        (meta, messages)
        meta: 会话级元数据字典
        messages: Message 实体列表
    """
    events: List[dict] = []
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as e:
            _log.warning("skip unparseable jsonl line: %s", e)

    return _parse_events(events)


def load_jsonl(path: str) -> Tuple[Dict[str, Any], List[Message]]:
    """从文件路径加载消息历史与元数据。"""
    _log.info("loading messages from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        return parse_jsonl(f.read())
