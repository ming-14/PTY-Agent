"""消息历史 JSONL 解析适配器。

将 <sessionId>.jsonl 解析为 List[Message]，并从 .meta 聚合 Session。

AtomCode JSONL 存储为每行一个回合（turn）的追加日志：
{
  "v": 1, "started_at": ms, "ts": ms, "iso": "...",
  "session_id": "...", "turn_id": N, "undone": false,
  "user": "输入", "assistant": "回复",
  "tools": [{"name": "...", "args": "...", "result": "...", "is_error": false}],
  "usage": {"prompt": N, "completion": N, "cached": N}
}

解析要点：
- 每行一个回合 → 产出 user + assistant 两条 Message
- 有工具调用时 assistant 消息含 tool_use + tool_result items
- 时间戳：毫秒 int（ts）+ ISO 字符串（iso）并存
- usage 在 usage 字段（prompt/completion/cached）
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from ..entities import (
    Message, MessageItem, Session, ToolResult, ToolUse, Usage,
)
from ..infra.logging import get_logger

_log = get_logger("messages_jsonl")


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


def _parse_args(args: Any) -> Dict[str, Any]:
    """解析工具参数字符串（JSON）为 dict。

    args 可能是 JSON 字符串或已解析 dict。
    """
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args.strip():
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return {"raw": args}
    return {}


def _parse_usage(raw: Optional[dict]) -> Optional[Usage]:
    """解析回合 usage（prompt/completion/cached）。"""
    if not raw:
        return None
    return Usage(
        input_tokens=int(raw.get("prompt", 0) or 0),
        output_tokens=int(raw.get("completion", 0) or 0),
        cached_input_tokens=int(raw.get("cached", 0) or 0),
    )


def _parse_tool_use(name: str, args: Any, call_id: str = "") -> ToolUse:
    """构建 ToolUse 实体。"""
    return ToolUse(
        tool_call_id=call_id,
        name=name,
        input=_parse_args(args),
    )


def _parse_tool_result(name: str, result: Any, is_error: bool = False,
                       call_id: str = "") -> ToolResult:
    """构建 ToolResult 实体。

    错误/拒绝识别：
    - is_error=true：工具执行出错（error 截断）
    - result 含 "denied by user" / "rejected by user"：is_denied
    """
    error: Optional[str] = None
    is_denied = False
    lowered = str(result).lower()

    if is_error:
        error = str(result)[:200] if result else "tool error"
    elif _DENIED_RE.search(lowered):
        is_denied = True
        error = str(result)[:200] if result else "tool denied"

    return ToolResult(
        tool_call_id=call_id,
        name=name,
        success=not is_error and not is_denied,
        is_denied=is_denied,
        is_error=is_error,
        error=error,
        output=result,
    )


# tool_result 拒绝识别
_DENIED_RE = re.compile(r"denied by user|rejected by user", re.IGNORECASE)


def _parse_turn(turn: dict, index: int, session_id: str) -> Tuple[Optional[Message], Optional[Message]]:
    """解析单个回合为 (user 消息, assistant 消息)。

    Args:
        turn: JSONL 的一行（一个回合）
        index: 回合序号（从 0 开始，用于生成消息 ID）
        session_id: 会话 UUID

    Returns:
        (user_message, assistant_message)，无 assistant 时第二个为 None
    """
    ts = turn.get("ts", 0) or 0
    iso = turn.get("iso", "") or ""
    if not iso and ts:
        # 只有毫秒时间戳时生成 ISO
        iso = _dt.datetime.fromtimestamp(ts / 1000, tz=_dt.timezone.utc).isoformat()

    # user 消息
    user_msg = Message(
        id=f"{session_id}-u{index + 1}",
        role="user",
        ts=ts,
        ts_iso=iso,
        items=[],
    )
    user_text = turn.get("user", "") or ""
    if user_text:
        user_msg.items.append(MessageItem(type="text", text=user_text))

    # assistant 消息
    assistant_text = turn.get("assistant", "") or ""
    tools = turn.get("tools") or []
    if not assistant_text and not tools:
        return user_msg, None

    items: List[MessageItem] = []
    if assistant_text:
        items.append(MessageItem(type="text", text=assistant_text))

    for i, t in enumerate(tools):
        if not isinstance(t, dict):
            continue
        name = t.get("name", "")
        args = t.get("args", "")
        result = t.get("result")
        is_error = bool(t.get("is_error", False))
        call_id = t.get("id", "") or f"{session_id}-c{index + 1}-{i + 1}"

        items.append(MessageItem(
            type="tool_use",
            tool_use=_parse_tool_use(name, args, call_id),
        ))
        items.append(MessageItem(
            type="tool_result",
            tool_result=_parse_tool_result(name, result, is_error, call_id),
        ))

    usage = _parse_usage(turn.get("usage"))
    # 若回合无工具调用，assistant 消息的 finish_reason 为 stop；
    # 有工具调用时为 tool_calls（推测）
    finish = "tool_calls" if tools else "stop"

    assistant_msg = Message(
        id=f"{session_id}-a{index + 1}",
        role="assistant",
        ts=ts,
        ts_iso=iso,
        items=items,
        finish_reason=finish,
        usage=usage,
    )
    return user_msg, assistant_msg


def parse_jsonl(data: Union[str, bytes, List[dict]]) -> List[Message]:
    """从 jsonl 文本解析消息列表。

    Args:
        data: jsonl 文本内容

    Returns:
        Message 实体列表（保持原始顺序：每个回合 user + assistant）
    """
    turns: List[dict] = []
    if isinstance(data, list):
        turns = data
    else:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError as e:
                _log.warning("skip unparseable jsonl line: %s", e)

    messages: List[Message] = []
    session_id = ""
    for i, turn in enumerate(turns):
        if not isinstance(turn, dict):
            continue
        session_id = turn.get("session_id", "") or session_id
        user_msg, assistant_msg = _parse_turn(turn, i, session_id)
        messages.append(user_msg)
        if assistant_msg is not None:
            messages.append(assistant_msg)

    _log.info("parse_jsonl: %d messages from %d turns", len(messages), len(turns))
    return messages


def load_jsonl(path: str) -> List[Message]:
    """从文件路径加载消息历史。"""
    _log.info("loading messages from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        return parse_jsonl(f.read())


def load_meta_dict(path: str) -> dict:
    """从 .meta 文件路径加载会话级元数据（raw dict）。"""
    _log.debug("loading session meta from %s", path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("failed to load meta %s: %s", path, e)
        return {}


def parse_session_meta(meta: dict, messages: List[Message]) -> Session:
    """从 .meta 原始 dict 聚合 Session 实体。

    Args:
        meta: .meta 文件解析出的 dict
        messages: 已解析的消息列表（用于回退 started_at）

    Returns:
        Session 实体
    """
    # 从 turn_stats 聚合模型与用量
    model = ""
    provider = ""
    ctx_window = 0
    usage = Usage()
    for stat in meta.get("turn_stats", []) or []:
        if not isinstance(stat, dict):
            continue
        ctx_window = stat.get("ctx_window", 0) or ctx_window
        for mu in stat.get("model_usage", []) or []:
            if not isinstance(mu, dict):
                continue
            if mu.get("model_id"):
                model = mu["model_id"]
            if mu.get("provider_id"):
                provider = mu["provider_id"]
            tokens = mu.get("tokens") or {}
            usage.input_tokens += int(tokens.get("input", 0) or 0)
            usage.output_tokens += int(tokens.get("output", 0) or 0)
            usage.cached_input_tokens += int(tokens.get("cached_input", 0) or 0)

    started_at = meta.get("created_at", 0) or 0
    if not started_at and messages:
        started_at = messages[0].ts

    return Session(
        id=meta.get("id", ""),
        name=meta.get("name", ""),
        cwd=meta.get("working_dir", ""),
        started_at=started_at,
        status="completed" if meta.get("turn_count", 0) > 0 else "empty",
        model=model,
        model_provider=provider,
        cli_version="",
        turn_count=meta.get("turn_count", 0) or 0,
        message_count=meta.get("message_count", 0) or 0,
        ctx_window=ctx_window,
        usage=usage,
    )