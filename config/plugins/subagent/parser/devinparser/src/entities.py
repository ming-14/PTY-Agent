"""实体层：devinparser 的核心领域对象。

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
class Metrics:
    """单条 agent step 的 token 用量（devin transcript 的 step.metrics）。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class Usage:
    """会话级 token 用量（devin transcript 的 final_metrics）。"""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cached_tokens: int = 0
    total_steps: int = 0


# ──────────────────────────────────────────
# 会话元数据
# ──────────────────────────────────────────

@dataclass
class Session:
    """会话元数据，来源于 transcript 顶层与 sessions.db 索引。"""
    id: str
    started_at: str = ""
    status: str = ""
    model: str = ""
    model_provider: str = ""
    cli_version: str = ""
    source: str = ""
    cwd: str = ""
    backend_type: str = ""          # windsurf / codex 等
    agent_mode: str = ""            # normal / bypass / plan
    title: str = ""
    usage: Usage = field(default_factory=Usage)


# ──────────────────────────────────────────
# 消息内容项
# ──────────────────────────────────────────

@dataclass
class ToolUse:
    """工具调用（agent 发起，devin 的 tool_calls 项）。"""
    tool_call_id: str
    name: str
    input: Dict[str, Any]


@dataclass
class ToolResult:
    """工具结果（agent 的 observation.results 项）。

    output 为工具原始输出文本（可能含 ANSI/VT 序列）。
    """
    tool_call_id: str
    name: str
    success: bool = True
    is_denied: bool = False
    is_error: bool = False
    error: Optional[str] = None
    output: str = ""
    raw: Any = None


@dataclass
class MessageItem:
    """消息内容项。

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
    """单条消息（user / assistant）。

    按 step 聚合：每条 user step → user 消息；
    每条 agent step → assistant 消息（含思考/文本/工具调用/工具结果）。
    """
    id: str
    role: str
    ts: int                      # 毫秒时间戳（由 ISO 转换）
    ts_iso: str
    items: List[MessageItem] = field(default_factory=list)
    model: Optional[str] = None
    metrics: Optional[Metrics] = None


# ──────────────────────────────────────────
# 实时状态（从屏幕快照解析）
# ──────────────────────────────────────────

@dataclass
class LiveState:
    """Devin TUI 当前实时状态，从屏幕快照解析。

    ai_status 取值：
    - idle: 空闲
    - thinking: 思考中
    - tool_running: 工具执行中
    - awaiting_approval: 权限请求框（等待用户批准工具）
    - asking: 提问框（ask_user_question，等待用户选择/回答）
    """
    ai_status: str = "idle"        # idle / thinking / tool_running / awaiting_approval
    input_text: str = ""
    context_percent: float = 0.0   # 上下文占用百分比
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
