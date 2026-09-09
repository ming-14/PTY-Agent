"""实体层：gooseparser 的核心领域对象。

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
    """token 用量。

    - input/output/total：常规输入输出总量
    - cache_read / cache_write：提示缓存命中与写入（input 的子集）
    - cost / cost_source：费用
    - elapsed_ms / time_to_first_token_ms：推理耗时统计（消息级）
    """
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    cost: float = 0.0
    cost_source: str = ""           # provider_reported / estimated
    elapsed_ms: Optional[int] = None
    time_to_first_token_ms: Optional[int] = None


# ──────────────────────────────────────────
# 会话元数据
# ──────────────────────────────────────────

@dataclass
class Session:
    """会话元数据，聚合自 sessions 表。"""
    id: str
    name: str = ""
    cwd: str = ""
    started_at: str = ""
    updated_at: str = ""
    status: str = ""
    model: str = ""
    provider: str = ""
    goose_mode: str = ""
    session_type: str = ""
    user_set_name: bool = False
    message_count: int = 0
    last_message_at: str = ""
    accumulated_cost: Optional[float] = None
    extension_data: Dict[str, Any] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    accumulated_usage: Usage = field(default_factory=Usage)


# ──────────────────────────────────────────
# 消息内容项
# ──────────────────────────────────────────

@dataclass
class ToolUse:
    """工具调用（assistant 发起，toolRequest block）。"""
    tool_call_id: str
    name: str
    input: Dict[str, Any]


@dataclass
class ToolResult:
    """工具结果（toolResponse block）。

    统一为：
    - output: 提取的文本内容（content 拼接 / structuredContent.stdout）
    - exit_code: structuredContent.exit_code
    - success / is_denied / is_error / error
    """
    tool_call_id: str
    name: str
    success: bool = True
    is_denied: bool = False
    is_error: bool = False
    error: Optional[str] = None
    output: str = ""
    exit_code: Optional[int] = None
    raw: Any = None


@dataclass
class MessageItem:
    """消息内容项，对应 content 数组的一个元素。

    type 取值：text / thinking / tool_use / tool_result / image / error
    - text/thinking/image/error: 用 text 字段
    - tool_use:    用 tool_use 字段
    - tool_result: 用 tool_result 字段
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
    ts: int                      # 毫秒时间戳（由 Unix 秒转换）
    ts_iso: str
    items: List[MessageItem] = field(default_factory=list)
    model: Optional[str] = None
    user_visible: bool = True
    turn_context: bool = False
    usage: Optional[Usage] = None


# ──────────────────────────────────────────
# 实时状态（从屏幕快照解析）
# ──────────────────────────────────────────

@dataclass
class LiveState:
    """Goose session TUI 当前实时状态，从屏幕快照解析。"""
    ai_status: str = "idle"       # idle / thinking / tool_running / awaiting_approval
    input_text: str = ""
    context_percent: float = 0.0
    screen_type: str = ""         # main（欢迎页） / conversation
    model_display: str = ""
    cwd_display: str = ""
    session_title: str = ""
    elapsed_seconds: Optional[float] = None


# ──────────────────────────────────────────
# 解析结果
# ──────────────────────────────────────────

@dataclass
class ParseResult:
    """解析器最终输出。"""
    session: Session
    messages: List[Message] = field(default_factory=list)
    live_state: Optional[LiveState] = None
