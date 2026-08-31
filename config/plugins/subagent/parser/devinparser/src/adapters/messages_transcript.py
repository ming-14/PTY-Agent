"""消息历史 Transcript JSON 解析适配器。

将 <session-name>.json（ATIF-v1.7 格式）解析为 (Session, List[Message])。

Devin 存储为 ATIF-v1.7 格式：
- steps: 会话步骤列表（system / user / agent）
- agent: 代理信息（name / version / model_name / tool_definitions）
- final_metrics: 会话级 token 统计

解析要点：
- system 消息为系统提示/指令，不列为消息
- user 消息直接转 message
- agent 消息聚合：message → text，reasoning_content → thinking，
  tool_calls → tool_use，observation.results → tool_result（按 source_call_id 匹配）
- 时间戳为 ISO 字符串 → 转毫秒 int + 保留 ISO
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ..entities import (
    Message, MessageItem, Metrics, ToolResult, ToolUse,
)
from ..infra.logging import get_logger

_log = get_logger("messages_transcript")

# 系统注入消息关键词（会话开头，非真实对话）
_SYSTEM_SOURCES = frozenset(("system",))


def _iso_to_ts(iso: str) -> Tuple[int, str]:
    """ISO 8601 时间戳 → (毫秒 int, 原样 ISO 字符串)。

    无法解析时返回 (0, 原字符串)。
    """
    if not iso:
        return 0, iso
    try:
        # 处理含时区格式：2026-08-15T06:10:11.032257100+00:00
        dt = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        ts = int(dt.timestamp() * 1000)
        return ts, iso
    except (ValueError, TypeError):
        _log.warning("unparseable timestamp: %r", iso)
        return 0, iso


def _parse_metrics(raw: Optional[dict]) -> Optional[Metrics]:
    """解析 step.metrics（devin 的 prompt/completion/cached tokens）。"""
    if not raw:
        return None
    return Metrics(
        prompt_tokens=raw.get("prompt_tokens", 0),
        completion_tokens=raw.get("completion_tokens", 0),
        cached_tokens=raw.get("cached_tokens", 0),
    )


def _parse_tool_use(tc: dict) -> ToolUse:
    """解析 tool_calls 项。"""
    return ToolUse(
        tool_call_id=tc.get("tool_call_id", ""),
        name=tc.get("function_name", ""),
        input=tc.get("arguments") or {},
    )


def _parse_tool_result(obs: dict, tool_names: Dict[str, str]) -> ToolResult:
    """解析 observation.results 项，按 source_call_id 回填 name。"""
    call_id = obs.get("source_call_id", "")
    content = obs.get("content", "")
    name = tool_names.get(call_id, "")
    # 尝试从内容中检测错误/拒绝
    is_denied = "denied by user" in content or "rejected by user" in content
    is_error = not is_denied and ("Exit code: " in content and "Exit code: 0" not in content)
    error = None
    if is_error:
        # 提取 exit code 错误信息
        m = re.search(r"Exit code: (\d+)", content)
        if m:
            error = f"Command exited with code {m.group(1)}"
    if is_denied:
        error = "exec command rejected by user"

    return ToolResult(
        tool_call_id=call_id,
        name=name,
        success=not is_error and not is_denied,
        is_denied=is_denied,
        is_error=is_error,
        error=error,
        output=content,
        raw=obs,
    )


def _build_assistant_message(step: dict, tool_names: Dict[str, str]) -> Message:
    """从 agent step 构建 assistant 消息。

    消息 items 按序聚合：
    1. reasoning_content → thinking（如有）
    2. message → text（如有）
    3. tool_calls → tool_use（如有）
    4. observation.results → tool_result（如有）
    """
    ts, ts_iso = _iso_to_ts(step.get("timestamp", ""))
    items: List[MessageItem] = []

    # 1. 思考过程
    reasoning = step.get("reasoning_content")
    if reasoning and reasoning.strip():
        items.append(MessageItem(type="thinking", text=reasoning))

    # 2. 回复文本
    msg = step.get("message", "")
    if msg and msg.strip():
        items.append(MessageItem(type="text", text=msg))

    # 3. 工具调用
    tool_calls = step.get("tool_calls") or []
    for tc in tool_calls:
        if isinstance(tc, dict):
            tool_use = _parse_tool_use(tc)
            tool_names[tool_use.tool_call_id] = tool_use.name
            items.append(MessageItem(type="tool_use", tool_use=tool_use))

    # 4. 工具结果
    observation = step.get("observation")
    if observation and isinstance(observation, dict):
        results = observation.get("results") or []
        for obs_item in results:
            if isinstance(obs_item, dict):
                items.append(MessageItem(
                    type="tool_result",
                    tool_result=_parse_tool_result(obs_item, tool_names),
                ))

    return Message(
        id=step.get("step_id", ""),
        role="assistant",
        ts=ts,
        ts_iso=ts_iso,
        items=items,
        model=step.get("model_name"),
        metrics=_parse_metrics(step.get("metrics")),
    )


def _build_user_message(step: dict) -> Message:
    """从 user step 构建 user 消息。"""
    ts, ts_iso = _iso_to_ts(step.get("timestamp", ""))
    msg = step.get("message", "")
    return Message(
        id=step.get("step_id", ""),
        role="user",
        ts=ts,
        ts_iso=ts_iso,
        items=[MessageItem(type="text", text=msg)] if msg else [],
    )


def parse_transcript(data: dict) -> Tuple[Dict[str, Any], List[Message]]:
    """从已加载的 transcript dict 解析会话元数据与消息列表。

    Args:
        data: transcript JSON 的内容

    Returns:
        (session 元数据 dict, Message 实体列表)
    """
    agent = data.get("agent") or {}
    session_meta: Dict[str, Any] = {
        "id": data.get("session_id", ""),
        "model": agent.get("model_name", ""),
        "cli_version": agent.get("version", ""),
        "source": "cli",
    }
    final_metrics = data.get("final_metrics") or {}

    steps = data.get("steps") or []
    messages: List[Message] = []
    tool_names: Dict[str, str] = {}  # tool_call_id → name（回填 tool_result）

    for step in steps:
        source = step.get("source", "")
        if source == "system":
            # 系统消息（系统提示/指令/技能定义），过滤不列入消息列表
            continue
        if source == "user":
            messages.append(_build_user_message(step))
        elif source == "agent":
            messages.append(_build_assistant_message(step, tool_names))
        else:
            _log.warning("unknown step source: %s", source)

    session_meta["title"] = _extract_title(messages)
    session_meta["total_prompt_tokens"] = final_metrics.get("total_prompt_tokens", 0)
    session_meta["total_completion_tokens"] = final_metrics.get("total_completion_tokens", 0)
    session_meta["total_cached_tokens"] = final_metrics.get("total_cached_tokens", 0)
    session_meta["total_steps"] = final_metrics.get("total_steps", len(steps))

    _log.info("parse_transcript: %d messages from %d steps",
              len(messages), len(steps))
    return session_meta, messages


def _extract_title(messages: List[Message]) -> str:
    """从首条用户消息提取会话标题。"""
    for m in messages:
        if m.role == "user" and m.items:
            text = m.items[0].text or ""
            return text[:60] if text else ""
    return ""


def load_transcript(path: str) -> Tuple[Dict[str, Any], List[Message]]:
    """从文件路径加载 transcript。

    Args:
        path: <session-name>.json 文件路径

    Returns:
        (session 元数据 dict, Message 实体列表)
    """
    _log.info("loading transcript from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return parse_transcript(data)