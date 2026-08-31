"""消息历史 JSONL 解析适配器。

将 <sessionId>.jsonl 解析为 (meta dict, List[Message])。

WorkBuddy (CodeBuddy Code) 存储为每行一个 JSON 事件的追加日志，事件类型：
- message (user / assistant)：对话消息
- reasoning：思考过程
- function_call：工具调用
- function_call_result：工具结果
- file-history-snapshot：文件快照（忽略）
- ai-title：AI 生成的会话标题
- resend-fork-notice：分叉/重发通知（忽略）

回合结构（parentId 链接）：
```
user → reasoning → message(assistant) + function_call* → function_call_result*
                    → reasoning → message(assistant) + ...
```
每个 "response cycle"（reasoning → message + function_call → result）聚合为
一条 assistant 消息；user 消息独立成条。

解析要点：
- 时间戳为毫秒 int（Unix epoch ms）
- 工具名通过 callId 从 function_call → function_call_result 匹配
- usage 在 providerData.rawUsage
- 系统注入的 user 消息（<system-reminder> 开头）过滤
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ..entities import (
    Message, MessageItem, Session, ToolResult, ToolUse, Usage,
)
from ..infra.logging import get_logger

_log = get_logger("messages_jsonl")

# 过滤的配置类事件（不产生 Message）
_SKIP_TYPES = frozenset((
    "file-history-snapshot", "resend-fork-notice",
))

# 拒绝/错误识别
_DENIED_RE = re.compile(r"denied by user|rejected by user", re.IGNORECASE)

# 系统注入的 user 消息内容前缀（过滤）
_SYSTEM_PREFIXES = (
    "<system-reminder",
    "<identity_context",
    "<conversation_history_summary>",
)

# 用户真实查询提取正则（在 system-reminder 块内）
_USER_QUERY_RE = re.compile(r"<user_query>(.*?)</user_query>", re.DOTALL)


def _parse_ts(ts: Any) -> Tuple[int, str]:
    """时间戳 → (毫秒 int, ISO 字符串)。

    CodeBuddy 使用毫秒 int（Unix epoch ms），也支持字符串。
    """
    if not ts:
        return 0, ""
    try:
        ts_ms = int(ts)
        return ts_ms, ""
    except (ValueError, TypeError):
        return 0, str(ts)


def _parse_usage(raw: Optional[dict]) -> Optional[Usage]:
    """解析 providerData.rawUsage。

    rawUsage 字段形态（真实数据实测）：
    prompt_tokens / completion_tokens / total_tokens /
    cached_tokens / prompt_cache_hit_tokens / prompt_cache_miss_tokens /
    cache_read_input_tokens / cache_creation_input_tokens /
    completion_thinking_tokens / credit
    """
    if not raw:
        return None
    details = raw.get("prompt_tokens_details")
    cached = raw.get("cached_tokens", 0)
    if not cached and isinstance(details, dict):
        cached = details.get("cached_tokens", 0)
    return Usage(
        input_tokens=raw.get("prompt_tokens", 0),
        output_tokens=raw.get("completion_tokens", 0),
        total_tokens=raw.get("total_tokens", 0),
        cached_tokens=cached,
    )


def _parse_arguments(arguments: Any) -> Dict[str, Any]:
    """解析 function_call 的 arguments。

    CodeBuddy 的 arguments 是 JSON 字符串。
    """
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {"raw": arguments}
    if isinstance(arguments, dict):
        return arguments
    return {}


def _parse_function_call(event: dict) -> ToolUse:
    """解析 function_call 事件为 ToolUse。"""
    return ToolUse(
        tool_call_id=event.get("callId", ""),
        name=event.get("name", ""),
        input=_parse_arguments(event.get("arguments")),
    )


def _parse_function_call_result(event: dict, tool_names: Dict[str, str]) -> ToolResult:
    """解析 function_call_result 事件为 ToolResult。"""
    call_id = event.get("callId", "")
    name = event.get("name", "") or tool_names.get(call_id, "")
    raw_output = event.get("output")
    status = event.get("status", "completed")

    output_text = ""
    exit_code = None
    is_error = False
    is_denied = False
    error = None

    if isinstance(raw_output, dict):
        output_text = raw_output.get("text", "")
        # 从 output.text 中提取 Exit Code
        exit_match = re.search(r"Exit Code:\s*(-?\d+)", output_text)
        if exit_match:
            exit_code = int(exit_match.group(1))
        # 识别拒绝
        if _DENIED_RE.search(output_text):
            is_denied = True
        # 识别错误
        if status == "incomplete":
            is_error = True
        elif exit_code is not None and exit_code != 0:
            is_error = True
        if is_error and not is_denied:
            error = output_text[:200] if output_text else "unknown error"
    elif isinstance(raw_output, str):
        output_text = raw_output
        if _DENIED_RE.search(output_text):
            is_denied = True
    else:
        output_text = str(raw_output) if raw_output is not None else ""

    success = not is_denied and not is_error

    return ToolResult(
        tool_call_id=call_id,
        name=name,
        success=success,
        is_denied=is_denied,
        is_error=is_error,
        error=error,
        output=raw_output,
        output_text=output_text,
        exit_code=exit_code,
    )


def _extract_user_query(text: str) -> Optional[str]:
    """从 system-reminder 侵入的 user 消息中提取真实用户输入。

    CodeBuddy 将系统上下文和用户输入合并为一条 input_text，
    用户输入在 `<user_query>...</user_query>` 标签内。
    如果无此标签，说明是纯用户输入。
    """
    if not text:
        return None
    m = _USER_QUERY_RE.search(text)
    if m:
        query = m.group(1).strip()
        return query if query else None
    # 纯 system-reminder 无 user_query → 系统消息
    if text.startswith("<system-reminder"):
        return None
    # 普通用户输入
    return text


def _is_system_message(text: str) -> bool:
    """判断是否应该被过滤的系统消息。

    纯系统消息（有 <system-reminder> 但无 <user_query>）才过滤。
    """
    if not text:
        return True
    if text.startswith("<system-reminder"):
        return "<user_query>" not in text
    return text.startswith("<conversation_history_summary>")


def _extract_reasoning_text(ev: dict) -> List[str]:
    """从 reasoning 事件提取思考文本列表。

    CodeBuddy 的 reasoning 事件：
    - rawContent: [{type: "reasoning_text", text: "..."}]  ← 主源
    - content: 通常为空数组，部分事件含 reasoning_text
    """
    texts: List[str] = []
    raw = ev.get("rawContent") or []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("type") == "reasoning_text":
                t = item.get("text", "")
                if t:
                    texts.append(t)
    if not texts:
        content = ev.get("content") or []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "reasoning_text":
                    t = item.get("text", "")
                    if t:
                        texts.append(t)
    return texts


def _parse_events(events: List[dict]) -> Tuple[List[Message], dict]:
    """从事件列表解析消息列表与元数据。

    聚合规则：
    - user message 事件 → 独立 user Message（系统注入过滤）
    - 每个 response cycle（reasoning → message + function_call → result）
      聚合为一条 assistant Message
    """
    meta: Dict[str, Any] = {}
    messages: List[Message] = []
    tool_names: Dict[str, str] = {}   # callId → tool name

    current: Optional[Message] = None   # 正在构建的 assistant 消息
    pending_thinking: List[str] = []    # 待附加的思考文本
    pending_model: Optional[str] = None  # 待附加的模型（来自 reasoning）

    def _finalize_current() -> None:
        nonlocal current, pending_thinking, pending_model
        if current is not None and current.items:
            messages.append(current)
        current = None
        pending_thinking = []
        pending_model = None

    first_ts = 0

    for ev in events:
        etype = ev.get("type", "")

        if etype in _SKIP_TYPES:
            continue

        if etype == "ai-title":
            meta.setdefault("title", ev.get("aiTitle", ""))
            continue

        if etype == "message":
            role = ev.get("role", "")
            ts, ts_iso = _parse_ts(ev.get("timestamp"))
            if first_ts == 0:
                first_ts = ts

            if role == "user":
                # 结束上一个 assistant cycle
                _finalize_current()
                items: List[MessageItem] = []
                is_system = False
                for item in ev.get("content", []) or []:
                    if not isinstance(item, dict):
                        continue
                    ctype = item.get("type")
                    text = item.get("text", "")
                    if ctype == "input_text":
                        # 提取 <user_query> 内的真实输入
                        query = _extract_user_query(text)
                        if query is None:
                            is_system = True
                        elif query:
                            items.append(MessageItem(type="text", text=query))
                    elif ctype == "image_blob_ref":
                        items.append(MessageItem(type="image", text=text))
                if not is_system and items:
                    messages.append(Message(
                        id=ev.get("id", ""),
                        role="user",
                        ts=ts,
                        ts_iso=ts_iso,
                        items=items,
                    ))

            elif role == "assistant":
                # 新的 assistant cycle：结束上一个
                _finalize_current()
                current = Message(
                    id=ev.get("id", ""),
                    role="assistant",
                    ts=ts,
                    ts_iso=ts_iso,
                )
                # 思考文本附加在文本前
                if pending_thinking:
                    current.items.append(MessageItem(
                        type="thinking",
                        text="\n".join(pending_thinking),
                    ))
                    pending_thinking = []
                for item in ev.get("content", []) or []:
                    if isinstance(item, dict) and item.get("type") == "output_text":
                        current.items.append(MessageItem(
                            type="text",
                            text=item.get("text", ""),
                        ))
                pd = ev.get("providerData") or {}
                current.model = pd.get("model") or pending_model
                raw_usage = pd.get("rawUsage")
                if raw_usage:
                    current.usage = _parse_usage(raw_usage)
                current.status = ev.get("status")
                pending_model = None
                if current.model:
                    meta.setdefault("model", current.model)
            continue

        if etype == "reasoning":
            # 若当前已有未附加思考的 cycle（前一个 reasoning 未被消费），
            # 且尚无 assistant message，则合并
            texts = _extract_reasoning_text(ev)
            if texts:
                pending_thinking = texts
            pd = ev.get("providerData") or {}
            model = pd.get("model")
            if model:
                meta.setdefault("model", model)
                pending_model = model
            continue

        if etype == "function_call":
            tool = _parse_function_call(ev)
            tool_names[tool.tool_call_id] = tool.name
            if current is None:
                # 无前序 assistant message（快速工具调用）：
                # 以 function_call 为锚创建 cycle
                ts, ts_iso = _parse_ts(ev.get("timestamp"))
                current = Message(id="", role="assistant", ts=ts, ts_iso=ts_iso)
                if pending_thinking:
                    current.items.append(MessageItem(
                        type="thinking",
                        text="\n".join(pending_thinking),
                    ))
                    pending_thinking = []
                current.model = pending_model
                pending_model = None
            # 真实数据中 rawUsage 挂在 function_call 的 providerData 上
            pd = ev.get("providerData") or {}
            if not current.usage and pd.get("rawUsage"):
                current.usage = _parse_usage(pd["rawUsage"])
            if not current.model and pd.get("model"):
                current.model = pd["model"]
                meta.setdefault("model", current.model)
            current.items.append(MessageItem(type="tool_use", tool_use=tool))
            continue

        if etype == "function_call_result":
            tr = _parse_function_call_result(ev, tool_names)
            if current is None:
                ts, ts_iso = _parse_ts(ev.get("timestamp"))
                current = Message(id="", role="assistant", ts=ts, ts_iso=ts_iso)
            # rawUsage 也可能挂在 function_call_result 上
            pd = ev.get("providerData") or {}
            if not current.usage and pd.get("rawUsage"):
                current.usage = _parse_usage(pd["rawUsage"])
            current.items.append(MessageItem(type="tool_result", tool_result=tr))
            continue

        _log.debug("unhandled event type: %s", etype)

    # 收尾：最后一条 assistant cycle
    _finalize_current()

    if first_ts:
        meta["started_at"] = str(first_ts)

    _log.info("parse_jsonl: %d messages from %d events", len(messages), len(events))
    return messages, meta


def parse_jsonl(data: str) -> Tuple[List[Message], dict]:
    """从 jsonl 文本解析消息列表与会话元数据。

    Args:
        data: jsonl 文本内容

    Returns:
        (messages, meta)
        messages: Message 实体列表
        meta: 会话级元数据字典
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


def load_jsonl(path: str) -> Tuple[List[Message], dict]:
    """从文件路径加载消息历史和元数据。"""
    _log.info("loading messages from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        return parse_jsonl(f.read())
