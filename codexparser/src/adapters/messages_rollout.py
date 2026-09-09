"""消息历史 rollout JSONL 解析适配器。

将 rollout-<ts>-<uuid>.jsonl 解析为 (Session 元数据 dict, List[Message])。

Codex 存储为每行一个 JSON 事件的追加日志，事件类型：
- session_meta：会话元数据（id / cwd / cli_version / model_provider 等）
- response_item：核心消息流
  - message（role=user）：content 为 input_text（用户输入）
  - message（role=assistant）：content 为 output_text（助手回复）
  - reasoning：content 为 reasoning_text[]（思考过程，流式分片 → 合并）
  - function_call：工具调用（name / arguments / call_id）
  - function_call_output：工具结果（call_id / output）
- event_msg：user_message / agent_message / token_count / turn_aborted 等辅助事件
- turn_context：回合上下文（model / approval_policy / sandbox_policy）
- world_state：环境状态快照（full）

解析要点：
- 消息按回合聚合：每条真实用户输入 → user 消息；其后到下一用户输入前的
  所有助手产物（reasoning / function_call / function_call_output / output_text）
  聚合为一条 assistant 消息
- reasoning 去重：流式分片（片段数组）后紧跟合并版（单条全文），
  同一文本只保留一次（按全文去重），避免 thinking 重复
- 系统注入的初始 user 消息（AGENTS.md 指令 / <environment_context>）过滤，
  不列入消息列表
- 时间戳为 ISO 字符串 → 转毫秒 int + 保留 ISO
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Dict, List, Optional, Tuple

from ..entities import Message, MessageItem, ToolResult, ToolUse
from ..infra.logging import get_logger

_log = get_logger("messages_rollout")

# 系统注入消息标记（会话开头，非真实用户输入）
_SYSTEM_INJECT_MARKS = (
    "# AGENTS.md instructions for",
    "<environment_context>",
)

# 输入框 placeholder（codex TUI 空闲提示）
INPUT_PLACEHOLDER = "Implement {feature}"


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


def _parse_arguments(arguments: str) -> Dict[str, Any]:
    """解析 function_call 的 arguments（JSON 字符串）。

    解析失败时返回空 dict（原始字符串存 raw_arguments）。
    """
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except (json.JSONDecodeError, TypeError):
        _log.warning("function arguments not JSON: %r", arguments[:120])
        return {}


def _parse_tool_result_output(output: str, name: str) -> Tuple[bool, bool, Optional[str], str]:
    """解析 function_call_output 的 output 文本。

    output 常为 JSON 字符串（如 {"output": "...", "metadata": {...}}），
    提取其中的文本；含 "rejected by user" 视为权限拒绝。

    Returns:
        (success, is_denied, error, text)
    """
    text = output or ""
    is_denied = "rejected by user" in text or "denied by user" in text
    error: Optional[str] = None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            if parsed.get("error"):
                error = str(parsed["error"])
                return False, is_denied, error, text
            text = str(parsed.get("output", text))
        elif isinstance(parsed, str):
            text = parsed
    except (json.JSONDecodeError, TypeError):
        pass

    if is_denied:
        return False, True, "exec command rejected by user", text
    if error:
        return False, False, error, text
    return True, False, None, text


def _parse_tool_result_meta(output: str) -> Tuple[Optional[int], Optional[float]]:
    """解析 function_call_output 的 metadata（exit_code / duration_seconds）。"""
    try:
        parsed = json.loads(output or "")
    except (json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    meta = parsed.get("metadata")
    if not isinstance(meta, dict):
        return None, None
    exit_code = meta.get("exit_code")
    duration = meta.get("duration_seconds")
    return (
        int(exit_code) if isinstance(exit_code, int) else None,
        float(duration) if isinstance(duration, (int, float)) else None,
    )


def _group_events(events: List[dict]) -> Dict[str, Any]:
    """遍历事件流，按回合聚合消息与提取元数据。

    Returns:
        {"session": dict, "messages": List[Message]}
    """
    session: Dict[str, Any] = {}
    messages: List[Message] = []
    seen_reasoning: set = set()

    # 当前回合的聚合状态
    cur_user: Optional[Message] = None
    cur_assistant: Optional[Message] = None
    seq = 0

    def new_assistant(ts: int, ts_iso: str, model: Optional[str]) -> Message:
        nonlocal seq
        seq += 1
        return Message(
            id=f"{session.get('id', '')}-a{seq}",
            role="assistant",
            ts=ts,
            ts_iso=ts_iso,
            items=[],
            model=model,
        )

    for ev in events:
        etype = ev.get("type", "")
        payload = ev.get("payload") or {}
        ts, ts_iso = _iso_to_ts(ev.get("timestamp", ""))

        if etype == "session_meta":
            session["id"] = payload.get("id", "")
            session["started_at"] = payload.get("timestamp", "")
            session["cwd"] = payload.get("cwd", "")
            session["cli_version"] = payload.get("cli_version", "")
            session["source"] = payload.get("source", "")
            session["model_provider"] = payload.get("model_provider", "")
            continue

        if etype == "turn_context":
            if payload.get("model"):
                session.setdefault("model", payload["model"])
            if payload.get("approval_policy"):
                session.setdefault("approval_policy", payload["approval_policy"])
            if payload.get("sandbox_policy") is not None:
                session.setdefault("sandbox_policy", payload["sandbox_policy"])
            continue

        if etype == "event_msg":
            mtype = payload.get("type", "")
            # 回合生命周期：上下文窗口 / 失败错误
            if mtype == "task_started":
                cw = payload.get("model_context_window")
                if cw:
                    session.setdefault("context_window", cw)
            elif mtype == "task_complete":
                err = payload.get("error")
                if err:
                    session["status"] = "failed"
                    session.setdefault("last_error", err.get("message", ""))
            continue

        if etype != "response_item":
            continue

        ptype = payload.get("type", "")

        # 用户消息：开始新回合
        if ptype == "message" and payload.get("role") == "user":
            for item in payload.get("content") or []:
                if item.get("type") != "input_text":
                    continue
                text = item.get("text", "")
                if _is_system_injected(text):
                    continue
                # 收尾上一个回合的 assistant 消息（有内容才追加）
                if cur_assistant is not None and cur_assistant.items:
                    messages.append(cur_assistant)
                seq += 1
                cur_user = Message(
                    id=f"{session.get('id', '')}-u{seq}",
                    role="user",
                    ts=ts,
                    ts_iso=ts_iso,
                    items=[MessageItem(type="text", text=text)],
                )
                messages.append(cur_user)
                # 新回合：重置助手聚合
                cur_assistant = None
                seen_reasoning.clear()
            continue

        if ptype == "message" and payload.get("role") == "assistant":
            if cur_assistant is None:
                cur_assistant = new_assistant(ts, ts_iso, None)
            for item in payload.get("content") or []:
                if item.get("type") != "output_text":
                    continue
                text = item.get("text", "")
                if text and text.strip():
                    cur_assistant.items.append(MessageItem(type="text", text=text))
            continue

        # 思考过程：按全文去重（分片 → 合并版）
        if ptype == "reasoning":
            full_text = "".join(
                c.get("text", "") for c in (payload.get("content") or [])
                if isinstance(c, dict)
            )
            if full_text and full_text not in seen_reasoning:
                seen_reasoning.add(full_text)
                if cur_assistant is None:
                    cur_assistant = new_assistant(ts, ts_iso, None)
                cur_assistant.items.append(MessageItem(type="thinking", text=full_text))
            continue

        # 工具调用
        if ptype == "function_call":
            if cur_assistant is None:
                cur_assistant = new_assistant(ts, ts_iso, None)
            arguments = payload.get("arguments", "")
            cur_assistant.items.append(MessageItem(
                type="tool_use",
                tool_use=ToolUse(
                    tool_call_id=payload.get("call_id", ""),
                    name=payload.get("name", ""),
                    input=_parse_arguments(arguments),
                    raw_arguments=arguments,
                ),
            ))
            continue

        # 工具结果
        if ptype == "function_call_output":
            if cur_assistant is None:
                cur_assistant = new_assistant(ts, ts_iso, None)
            call_id = payload.get("call_id", "")
            output = payload.get("output", "")
            name = ""
            # 回填工具名（按 call_id 匹配同回合内的 tool_use）
            for item in cur_assistant.items:
                if item.type == "tool_use" and item.tool_use \
                        and item.tool_use.tool_call_id == call_id:
                    name = item.tool_use.name
                    break
            success, is_denied, error, text = _parse_tool_result_output(output, name)
            exit_code, duration = _parse_tool_result_meta(output)
            cur_assistant.items.append(MessageItem(
                type="tool_result",
                tool_result=ToolResult(
                    tool_call_id=call_id,
                    name=name,
                    success=success,
                    is_denied=is_denied,
                    is_error=error is not None,
                    error=error,
                    output=text,
                    exit_code=exit_code,
                    duration_seconds=duration,
                    raw=output,
                ),
            ))
            continue

        _log.debug("skip response_item type=%s", ptype)

    # 收尾：追加最后一条 assistant 消息
    if cur_assistant is not None and cur_assistant.items:
        messages.append(cur_assistant)

    # 会话标题：首条用户消息文本
    if not session.get("title"):
        for m in messages:
            if m.role == "user" and m.items and m.items[0].text:
                session["title"] = m.items[0].text[:60]
                break

    return {"session": session, "messages": messages}


def parse_rollout(data: str) -> Tuple[Dict[str, Any], List[Message]]:
    """从 rollout JSONL 文本解析。

    Args:
        data: rollout jsonl 文本内容

    Returns:
        (session 元数据 dict, Message 实体列表)
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

    result = _group_events(events)
    _log.info("parse_rollout: %d messages from %d events",
              len(result["messages"]), len(events))
    return result["session"], result["messages"]


def load_rollout(path: str) -> Tuple[Dict[str, Any], List[Message]]:
    """从文件路径加载 rollout。

    Args:
        path: rollout jsonl 文件路径

    Returns:
        (session 元数据 dict, Message 实体列表)
    """
    _log.info("loading rollout from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        return parse_rollout(f.read())
