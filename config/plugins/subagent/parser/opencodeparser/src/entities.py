"""实体层：opencodeparser 的核心领域对象。

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
    """会话级 / 单条消息的 token 用量。

    字段对齐 opencode 的 tokens 语义：
    - input / output / reasoning：常规输入输出与思考 token
    - cache_read / cache_write：提示缓存命中与写入
    """
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    total_cost: float = 0.0


# ──────────────────────────────────────────
# 会话元数据
# ──────────────────────────────────────────

@dataclass
class Session:
    """会话元数据，来源于 opencode.db 的 session 表。"""
    id: str
    slug: str = ""
    cwd: str = ""
    path: str = ""
    title: str = ""
    status: str = ""
    agent: str = ""
    model: str = ""
    model_provider: str = ""
    variant: str = ""
    version: str = ""
    cost: float = 0.0
    started_at: str = ""
    usage: Usage = field(default_factory=Usage)
    parent_id: Optional[str] = None
    permission: Any = None


# ──────────────────────────────────────────
# 消息内容项
# ──────────────────────────────────────────

@dataclass
class ToolUse:
    """工具调用（assistant 发起）。"""
    tool_call_id: str
    name: str
    input: Dict[str, Any]


@dataclass
class ToolResult:
    """工具结果（assistant 侧 tool part 的完成状态）。

    opencode 的 tool part 同时承载调用与结果（state 字段）：
    - status=completed：成功，含 output
    - status=error：失败，含 error
    - status=running：进行中（无 output）
    """
    tool_call_id: str
    name: str
    success: bool = True
    is_denied: bool = False
    is_error: bool = False
    error: Optional[str] = None
    output: Any = None
    raw: Any = None


@dataclass
class MessageItem:
    """消息内容项，对应 part 数组的一个元素。

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
    ts: int                      # 毫秒时间戳
    ts_iso: str
    items: List[MessageItem] = field(default_factory=list)
    model: Optional[str] = None
    provider: Optional[str] = None
    agent: Optional[str] = None
    finish: Optional[str] = None
    usage: Optional[Usage] = None
    parent_id: Optional[str] = None


# ──────────────────────────────────────────
# 实时状态（从屏幕快照解析）
# ──────────────────────────────────────────

@dataclass
class LiveState:
    """opencode TUI 当前实时状态，从屏幕快照解析。

    ai_status 取值：
    - idle：空闲
    - thinking：思考中
    - tool_running：工具执行中
    - awaiting_approval：权限请求（Permission required 框）
    - awaiting_answer：question 工具提问（等待用户选择）
    """
    ai_status: str = "idle"
    input_text: str = ""
    context_percent: float = 0.0   # 状态栏 "N% used"（右侧面板）
    context_tokens: int = 0        # 右侧面板 token 数
    cost_display: str = ""
    screen_type: str = ""          # main（欢迎页） / conversation（对话中）
    model_display: str = ""
    cwd_display: str = ""
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
