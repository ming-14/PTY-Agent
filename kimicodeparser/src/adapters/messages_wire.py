"""消息历史 wire.jsonl 解析适配器。

将 wire.jsonl（事件源格式）解析为 (meta dict, List[Message])。

Kimi Code 存储为每行一个 JSON 事件的追加日志，事件类型：
- metadata / runtime.set_binding / profile.bind / permission.set_mode：初始化
- prompt.accepted / turn.prompt / turn.ended：回合边界
- context.append_message：权威消息记录（user）
- context.append_loop_event：循环事件（step.begin / content.part / tool.call / tool.result / step.end）
- llm.request / usage.record / token_counting.*：LLM 与用量
- interaction.request / interaction.resolved / permission.record_approval_result：权限

回合结构（turnId 聚合）：
```
turn.prompt (user input)
  → context.append_message (user, role=user)
  → llm.request
  → loop_event: step.begin
      → content.part (think)    ← assistant 思考
      → content.part (text)     ← assistant 文本
    → loop_event: step.end
      → (furthur steps 同意回合内 tool calls)
  → turn.ended
```

聚合策略：按 turnId 聚合，每个 turn 产生一条 user + 一条 assistant 消息。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ..entities import (
    Message, MessageItem, ToolResult, ToolUse, Usage,
)
from ..infra.logging import get_logger

_log = get_logger("messages_wire")

# 权限拒绝关键词（tool.result 内容检测）
_DENIED_RE = re.compile(r"denied by user|rejected by user|dismissed", re.IGNORECASE)


def _parse_ts(ts: Any) -> Tuple[int, str]:
    """时间戳 → (毫秒 int, ISO 字符串)。

    Kimi Code 使用毫秒 int（Unix epoch ms）。
    """
    if not ts:
        return 0, ""
    try:
        ts_ms = int(ts)
        return ts_ms, ""
    except (ValueError, TypeError):
        return 0, str(ts)


def _parse_usage(raw: Optional[dict]) -> Optional[Usage]:
    """解析 usage 字典（step.end.usage 或 usage.record.usage）。

    Kimi Code 字段：
    - inputOther（输入）/ output（输出）
    - inputCacheRead（缓存命中）/ inputCacheCreation（缓存写入）
    """
    if not raw:
        return None
    return Usage(
        input_tokens=raw.get("inputOther", 0),
        output_tokens=raw.get("output", 0),
        cache_read_input_tokens=raw.get("inputCacheRead", 0),
        cache_write_input_tokens=raw.get("inputCacheCreation", 0),
    )


def _parse_events(events: List[dict]) -> Tuple[List[Message], dict]:
    """从事件列表解析消息列表与元数据。

    Args:
        events: wire.jsonl 事件列表

    Returns:
        (messages, meta)
        messages: Message 实体列表（按时间顺序）
        meta: 会话级元数据字典
    """
    meta: Dict[str, Any] = {}
    messages: List[Message] = []

    # 回合聚合状态
    current_turn_id: Optional[str] = None          # 当前回合 ID（字符串）
    current_user: Optional[Message] = None          # 当前回合 user 消息
    current_assistant: Optional[Message] = None     # 当前回合 assistant 消息（聚合中）
    current_turn_model: Optional[str] = None        # 当前回合模型（来自最近的 llm.request）
    current_turn_usage: Optional[Usage] = None      # 当前回合累计 usage

    # 工具关联状态
    tool_call_ids: Dict[str, str] = {}              # stepUuid → toolCallId
    pending_approvals: Dict[str, str] = {}          # toolCallId → decision（approved/denied）
    active_tool_call_id: Optional[str] = None       # 当前 step 中的 tool.callId

    # 会话级元数据
    meta["permission_mode"] = ""
    meta["started_at"] = ""

    def _finalize_assistant() -> None:
        """完成当前 assistant 消息并加入消息列表。"""
        nonlocal current_assistant
        if current_assistant is not None:
            if current_assistant.items:
                messages.append(current_assistant)
            current_assistant = None

    def _get_or_create_assistant(turn_id: str, ts: int, ts_iso: str) -> Message:
        """获取或创建当前回合的 assistant 消息。"""
        nonlocal current_assistant
        if current_assistant is None:
            current_assistant = Message(
                id="", role="assistant", ts=ts, ts_iso=ts_iso,
                model=current_turn_model, turn_id=turn_id,
            )
        return current_assistant

    seq = 0  # assistant 消息序号生成

    for ev in events:
        etype = ev.get("type", "")
        etime = ev.get("time", 0)
        ts, ts_iso = _parse_ts(etime)

        # ── 回合边界 ──

        if etype == "turn.prompt":
            # 收尾上一个回合
            _finalize_assistant()
            current_user = None
            current_turn_id = None
            current_turn_usage = None
            current_turn_model = None
            # 用户输入直接处理
            inputs = ev.get("input") or []
            for inp in inputs:
                if isinstance(inp, dict) and inp.get("type") == "text":
                    text = inp.get("text", "")
                    if text.strip():
                        # 过滤系统注入
                        origin = ev.get("origin", {})
                        if origin.get("kind") != "user":
                            continue
                        # 注意：文本可能到这里时是乱码（session 编码问题）
                        # 但 context.append_message 是权威源，所以这里只做占位
            continue

        if etype == "turn.ended":
            _finalize_assistant()
            continue

        # ── 模型元数据 ──

        if etype == "llm.request":
            model = ev.get("model", "")
            if model:
                current_turn_model = model
                meta.setdefault("model", model)
            provider = ev.get("provider", "")
            if provider:
                meta.setdefault("model_provider", provider)
            if ev.get("thinkingEffort"):
                meta.setdefault("thinking_effort", ev["thinkingEffort"])
            continue

        # ── 权限模式 ──

        if etype == "permission.set_mode":
            meta["permission_mode"] = ev.get("mode", "")
            continue

        # ── 权威消息（context.append_message） ──

        if etype == "context.append_message":
            msg = ev.get("message", {})
            role = msg.get("role", "")
            if role == "user":
                # 结束上一个 assistant
                _finalize_assistant()
                items: List[MessageItem] = []
                for item in msg.get("content", []):
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = (item.get("text", "") or "").strip()
                        if text:
                            items.append(MessageItem(type="text", text=text))
                if items:
                    current_user = Message(
                        id=msg.get("id", ""),
                        role="user",
                        ts=ts,
                        ts_iso=ts_iso,
                        items=items,
                    )
                    messages.append(current_user)
                    if not meta.get("started_at"):
                        meta["started_at"] = str(ts)
            elif role == "assistant":
                # Kimi 的 assistant 消息也可能直接出现在 append_message 中
                # 但实测数据中未出现，保留处理
                _finalize_assistant()
                items = []
                for item in msg.get("content", []):
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = (item.get("text", "") or "").strip()
                        if text:
                            items.append(MessageItem(type="text", text=text))
                if items:
                    seq += 1
                    current_assistant = Message(
                        id=msg.get("id", f"a{seq}"),
                        role="assistant",
                        ts=ts, ts_iso=ts_iso,
                        items=items,
                        model=current_turn_model,
                    )
            continue

        # ── 循环事件（context.append_loop_event） ──

        if etype == "context.append_loop_event":
            event = ev.get("event", {})
            etype2 = event.get("type", "")
            step_uuid = event.get("stepUuid", "")
            turn_id = event.get("turnId", "")
            current_turn_id = turn_id

            if etype2 == "step.begin":
                active_tool_call_id = None
                continue

            if etype2 == "content.part":
                part = event.get("part", {})
                ptype = part.get("type", "")

                if ptype == "think":
                    think_text = part.get("think", "")
                    if think_text:
                        asst = _get_or_create_assistant(turn_id, ts, ts_iso)
                        asst.items.append(MessageItem(type="thinking", text=think_text))

                elif ptype == "text":
                    text = part.get("text", "")
                    if text:
                        asst = _get_or_create_assistant(turn_id, ts, ts_iso)
                        asst.items.append(MessageItem(type="text", text=text))

                continue

            if etype2 == "tool.call":
                active_tool_call_id = event.get("toolCallId", "")
                asst = _get_or_create_assistant(turn_id, ts, ts_iso)
                asst.items.append(MessageItem(
                    type="tool_use",
                    tool_use=ToolUse(
                        tool_call_id=active_tool_call_id,
                        name=event.get("name", ""),
                        input=event.get("args", {}),
                    ),
                ))
                continue

            if etype2 == "tool.result":
                tool_call_id = event.get("toolCallId", "")
                parent_uuid = event.get("parentUuid", "")
                result = event.get("result", {})
                output = result.get("output", "")
                is_error = result.get("is_error", False)

                # 回填工具名（从同回合已添加的 tool_use 中查找）
                name = ""
                if asst := current_assistant:
                    for item in reversed(asst.items):
                        if item.type == "tool_use" and item.tool_use:
                            if item.tool_use.tool_call_id == tool_call_id:
                                name = item.tool_use.name
                                break

                # 判断拒绝/错误
                output_text = str(output) if output else ""
                is_denied = bool(_DENIED_RE.search(output_text))
                if not is_denied:
                    # 检查权限拒绝
                    decision = pending_approvals.get(tool_call_id)
                    if decision and decision != "approved":
                        is_denied = True

                approved = not is_denied
                if is_error and not is_denied:
                    approved = False

                asst = _get_or_create_assistant(turn_id, ts, ts_iso)
                asst.items.append(MessageItem(
                    type="tool_result",
                    tool_result=ToolResult(
                        tool_call_id=tool_call_id,
                        name=name,
                        success=not is_denied and not is_error,
                        is_denied=is_denied,
                        is_error=is_error or (not is_denied and bool(output_text) and len(output_text) > 2 and not output_text.startswith("total")),
                        error=None if approved else output_text[:200] if output_text else None,
                        output=output,
                        output_text=output_text,
                        approved=approved,
                    ),
                ))
                active_tool_call_id = None
                continue

            if etype2 == "step.end":
                step_usage = event.get("usage")
                if step_usage:
                    parsed = _parse_usage(step_usage)
                    if parsed and (parsed.input_tokens or parsed.output_tokens):
                        current_turn_usage = parsed
                        if current_assistant is not None:
                            current_assistant.usage = parsed
                continue

            _log.debug("unhandled loop_event subtype: %s", etype2)
            continue

        # ── 权限交互 ──

        if etype == "interaction.request":
            # 记录权限请求（toolName / toolCallId）
            tool_call_id = ev.get("toolCallId", "")
            if tool_call_id:
                pending_approvals[tool_call_id] = ev.get("request", {}).get("decision", "")
            continue

        if etype == "interaction.resolved":
            tool_call_id = None
            # 查找关联的 interaction.request
            req_id = ev.get("id", "")
            # 通过 id 匹配 interaction.request 来获取 toolCallId
            decision = ev.get("response", {}).get("decision", "")
            # 回填到所有匹配的 tool_result 中
            if decision:
                pending_approvals[req_id] = decision
            continue

        if etype == "permission.record_approval_result":
            tool_call_id = ev.get("toolCallId", "")
            decision = ev.get("result", {}).get("decision", "")
            if tool_call_id and decision:
                pending_approvals[tool_call_id] = decision
            continue

        # ── 用量记录 ──

        if etype == "usage.record":
            usage = ev.get("usage")
            if usage:
                # 会话级累计（不关联到具体消息）
                pass
            continue

        # ── 其他忽略事件 ──

        if etype in ("metadata", "runtime.set_binding", "profile.bind",
                     "prompt.accepted", "plugin.session_start",
                     "llm.tools_snapshot", "token_counting.measured",
                     "token_counting.turn_recorded", "config.update",
                     "tools.set_active_tools"):
            continue

        # 旧协议版本（1.4）的无消息事件，忽略
        _log.debug("unhandled event type: %s", etype)

    # 收尾最后一条 assistant
    _finalize_assistant()

    # 消息 ID 生成
    seq = 0
    for m in messages:
        if m.role == "assistant" and not m.id:
            seq += 1
            m.id = f"a{seq}"

    _log.info("parse_wire: %d messages from %d events", len(messages), len(events))
    return messages, meta


def parse_wire(data: str) -> Tuple[List[Message], dict]:
    """从 wire.jsonl 文本解析消息列表与会话元数据。

    Args:
        data: wire.jsonl 文本内容

    Returns:
        (messages, meta)
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


def load_wire(path: str) -> Tuple[List[Message], dict]:
    """从文件路径加载 wire.jsonl 消息历史和元数据。"""
    _log.info("loading wire from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        return parse_wire(f.read())