"""实体层：atomcodeparser 的核心领域对象。

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

    字段对齐 AtomCode JSONL 的 usage 语义：
    - input / output：常规输入输出
    - cached_input：缓存命中输入
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    total_cost: float = 0.0


# ──────────────────────────────────────────
# 会话元数据
# ──────────────────────────────────────────

@dataclass
class Session:
    """会话元数据，聚合自 .meta 文件与 jsonl 事件。"""
    id: str
    name: str = ""
    cwd: str = ""
    started_at: int = 0
    status: str = ""
    model: str = ""
    model_provider: str = ""
    cli_version: str = ""
    git_commit: str = ""
    turn_count: int = 0
    message_count: int = 0
    ctx_window: int = 0
    cost: float = 0.0
    usage: Usage = field(default_factory=Usage)


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
    """工具结果（assistant 侧 tool 元素的完成状态）。

    AtomCode JSONL 的 tools 元素同时承载调用与结果：
    - name + args：调用参数
    - result + is_error：结果状态
    """
    tool_call_id: str
    name: str
    success: bool = True
    is_denied: bool = False
    is_error: bool = False
    error: Optional[str] = None
    output: Any = None


@dataclass
class MessageItem:
    """消息内容项，对应 content 数组的一个元素。

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
    finish_reason: Optional[str] = None
    usage: Optional[Usage] = None


# ──────────────────────────────────────────
# 实时状态（从屏幕快照解析）
# ──────────────────────────────────────────

@dataclass
class LiveState:
    """AtomCode TUI 当前实时状态，从屏幕快照解析。

    ai_status 取值：
    - idle：空闲
    - thinking：思考中（/ Noodling... / - Pondering...）
    - tool_running：工具执行中
    """
    ai_status: str = "idle"
    input_text: str = ""
    context_percent: float = 0.0   # 状态栏 "N/262k tok (N%)"
    context_tokens: int = 0
    context_window: int = 0
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