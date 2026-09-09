"""实体层：kimicodeparser 的核心领域对象。

所有对象为纯数据结构，不依赖任何外部框架或 IO。
依赖规则：此层不导入 adapters / infra / usecases。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────
# Token / 用量相关
# ──────────────────────────────────────────

@dataclass
class Usage:
    """会话级 / 单条消息的 token 用量。

    字段对齐 Kimi Code 的 usage.record 语义：
    - input_tokens      = inputOther
    - output_tokens     = output
    - cache_read_input_tokens = inputCacheRead
    - cache_write_input_tokens = inputCacheCreation
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0


# ──────────────────────────────────────────
# 会话元数据
# ──────────────────────────────────────────

@dataclass
class Session:
    """会话元数据，聚合自 state.json 与 wire.jsonl 事件。"""
    id: str
    cwd: str = ""
    started_at: str = ""            # 毫秒时间戳字符串
    status: str = ""                # completed / failed（lastTurnReason）
    model: str = ""
    model_provider: str = ""
    cli_version: str = ""
    title: str = ""                 # state.json title
    is_custom_title: bool = False
    archived: bool = False
    permission_mode: str = ""       # manual / yolo / auto / plan
    usage: Usage = field(default_factory=Usage)


# ──────────────────────────────────────────
# 消息内容项
# ──────────────────────────────────────────

@dataclass
class ToolUse:
    """工具调用（loop_event tool.call）。"""
    tool_call_id: str
    name: str
    input: Dict[str, Any]


@dataclass
class ToolResult:
    """工具结果（loop_event tool.result）。"""
    tool_call_id: str
    name: str
    success: bool = True
    is_denied: bool = False
    is_error: bool = False
    error: Optional[str] = None
    output: Any = None              # 原始输出
    output_text: str = ""
    approved: Optional[bool] = None  # 权限批准结果（interaction.resolved decision）


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
    """单条消息（user / assistant）。"""
    id: str
    role: str
    ts: int                          # 毫秒时间戳
    ts_iso: str                      # ISO 字符串
    items: List[MessageItem] = field(default_factory=list)
    model: Optional[str] = None
    usage: Optional[Usage] = None
    turn_id: Optional[str] = None    # 所属回合 ID（Kimi turnId）


# ──────────────────────────────────────────
# 实时状态（从屏幕快照解析）
# ──────────────────────────────────────────

@dataclass
class LiveState:
    """Kimi Code TUI 当前实时状态，从屏幕快照解析。"""
    ai_status: str = "idle"          # idle / working / awaiting_approval / asking
    input_text: str = ""
    screen_type: str = ""            # main（欢迎页） / conversation
    model_display: str = ""
    cwd_display: str = ""
    context_percent: Optional[int] = None   # 状态栏 context: N%
    version_display: str = ""


# ──────────────────────────────────────────
# 解析结果
# ──────────────────────────────────────────

@dataclass
class ParseResult:
    """解析器最终输出。"""
    session: Session
    messages: List[Message] = field(default_factory=list)
    live_state: Optional[LiveState] = None
