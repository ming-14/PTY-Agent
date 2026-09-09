"""实体层：piparser 的核心领域对象。

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
    """单条消息 / 会话级 token 用量（pi 的 message.usage）。"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0


# ──────────────────────────────────────────
# 会话元数据
# ──────────────────────────────────────────

@dataclass
class Session:
    """会话元数据，来源于 JSONL header + 各 entry 聚合。"""
    id: str
    cwd: str = ""
    started_at: str = ""
    status: str = ""                 # idle / running
    model: str = ""                  # 最新 model_change 的 modelId
    model_provider: str = ""         # 最新 model_change 的 provider
    cli_version: str = ""
    thinking_level: str = ""         # 最新 thinking_level_change
    title: str = ""                  # session_info entry 的 name
    parent_session: str = ""         # header.parentSession（fork/clone 产生）
    source: str = ""
    usage: Usage = field(default_factory=Usage)


# ──────────────────────────────────────────
# 消息内容项
# ──────────────────────────────────────────

@dataclass
class ToolUse:
    """工具调用（assistant content 的 toolCall 项）。"""
    tool_call_id: str
    name: str
    input: Dict[str, Any]


@dataclass
class ToolResult:
    """工具结果（role=toolResult 独立消息）。

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
    """单条消息。

    Pi 中 role 取值：user / assistant / toolResult / bashExecution /
    custom / branchSummary / compactionSummary。
    - user:            items 为 text
    - assistant:       items 为 thinking / text / tool_use 混合
    - toolResult:      items 为单个 tool_result
    - bashExecution:   items 为单个 tool_result（bash ! 模式，含 command/output/exitCode）
    - custom:          items 为 text（扩展注入，LLM 可见）
    - branchSummary:   items 为 text（分支摘要）
    - compactionSummary: items 为 text（压缩摘要）
    """
    id: str
    role: str
    ts: int                      # 毫秒时间戳（message 内部或 entry 转换）
    ts_iso: str
    items: List[MessageItem] = field(default_factory=list)
    model: Optional[str] = None
    provider: Optional[str] = None
    api: Optional[str] = None
    stop_reason: Optional[str] = None
    response_id: Optional[str] = None
    usage: Optional[Usage] = None


# ──────────────────────────────────────────
# 实时状态（从屏幕快照解析）
# ──────────────────────────────────────────

@dataclass
class LiveState:
    """Pi TUI 当前实时状态，从屏幕快照解析。

    ai_status 取值：
    - idle: 空闲（无 Working spinner）
    - working: 工作中（⠋ Working... spinner）
    - tool_running: 工具执行中
    - awaiting_approval: 权限请求框（等待用户批准）
    """
    ai_status: str = "idle"        # idle / working / tool_running / awaiting_approval
    input_text: str = ""
    context_percent: float = 0.0   # 上下文占用百分比
    context_window: int = 0        # 上下文窗口大小（token）
    screen_type: str = ""          # main（欢迎页） / conversation（对话中）
    model_display: str = ""
    thinking_level: str = ""
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
