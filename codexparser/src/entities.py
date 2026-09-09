"""实体层：codex 解析器的核心领域对象。

所有对象为纯数据结构，不依赖任何外部框架或 IO。
依赖规则：此层不导入 adapters / infra / usecases。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────
# Token / 费用相关
# ──────────────────────────────────────────

@dataclass
class Usage:
    """会话级 token 用量与费用。

    codex 的 token_count 事件当前 info=null（模型未返回用量），
    字段保留以便将来填充。
    """
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0


# ──────────────────────────────────────────
# 会话元数据
# ──────────────────────────────────────────

@dataclass
class Session:
    """会话元数据，来源于 rollout 的 session_meta / turn_context / task_* 事件。"""
    id: str
    started_at: str = ""
    status: str = ""
    model: str = ""
    model_provider: str = ""
    cli_version: str = ""
    source: str = ""
    cwd: str = ""
    title: str = ""
    approval_policy: str = ""
    sandbox_policy: Any = None
    context_window: Optional[int] = None   # task_started.model_context_window
    last_error: Optional[str] = None       # task_complete.error.message（会话失败时）
    usage: Usage = field(default_factory=Usage)


# ──────────────────────────────────────────
# 消息内容项
# ──────────────────────────────────────────

@dataclass
class ToolUse:
    """工具调用（function_call）。"""
    tool_call_id: str
    name: str
    input: Dict[str, Any]
    raw_arguments: str = ""


@dataclass
class ToolResult:
    """工具结果（function_call_output）。

    output 为字符串，通常是 JSON 序列化文本（如 {"output": "..."}）；
    metadata 含工具执行信息（exit_code / duration_seconds）。
    """
    tool_call_id: str
    name: str
    success: bool = True
    is_denied: bool = False
    is_error: bool = False
    error: Optional[str] = None
    output: str = ""
    exit_code: Optional[int] = None
    duration_seconds: Optional[float] = None
    raw: Any = None


@dataclass
class MessageItem:
    """消息内容项，对应一次回复流中的一个元素。

    type 取值：text / thinking / tool_use / tool_result
    - text/thinking: 用 text 字段
    - tool_use:     用 tool_use 字段
    - tool_result:  用 tool_result 字段
    """
    type: str
    text: Optional[str] = None
    tool_use: Optional[ToolUse] = None
    tool_result: Optional[ToolResult] = None


# ──────────────────────────────────────────
# 消息
# ──────────────────────────────────────────

@dataclass
class Message:
    """单条消息。

    按回合聚合：每条用户输入对应一条 user 消息，
    其后同一回合内的所有助手产物（reasoning / function_call /
    function_call_output / output_text）聚合为一条 assistant 消息。
    """
    id: str
    role: str
    ts: int
    ts_iso: str
    items: List[MessageItem] = field(default_factory=list)
    model: Optional[str] = None


# ──────────────────────────────────────────
# 实时状态（从屏幕快照解析）
# ──────────────────────────────────────────

@dataclass
class LiveState:
    """codex TUI 当前实时状态，从屏幕快照解析。

    JSONL 不记录这些字段，需从屏幕补充。
    """
    ai_status: str = "idle"        # idle / thinking / tool_running / awaiting_approval
    input_text: str = ""
    context_percent: float = 0.0   # 状态栏 "N% context left"（剩余百分比）
    screen_type: str = ""          # main（欢迎页） / conversation（对话中）
    model_display: str = ""
    cwd_display: str = ""


# ──────────────────────────────────────────
# 解析结果
# ──────────────────────────────────────────

@dataclass
class ParseResult:
    """解析器最终输出。"""
    session: Session
    messages: List[Message] = field(default_factory=list)
    live_state: Optional[LiveState] = None
