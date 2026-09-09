"""实体层：geminiparser 的核心领域对象。

所有对象为纯数据结构，不依赖任何外部框架或 IO。
依赖规则：此层不导入 adapters / infra / usecases。

字段对齐 Gemini CLI JSONL 数据格式：
- tokens: {input, output, cached, thoughts, tool, total}
- toolCalls: {id, name, args, result[{functionResponse}], status}
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

    字段对齐 gemini 的 tokens 结构：
    {input, output, cached, thoughts, tool, total}
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thoughts_tokens: int = 0
    tool_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0


# ──────────────────────────────────────────
# 会话元数据
# ──────────────────────────────────────────

@dataclass
class Session:
    """会话元数据，聚合自 JSONL 首行与 events。"""
    id: str
    cwd: str = ""
    project: str = ""               # tmp/<project>
    started_at: str = ""            # ISO 时间戳
    status: str = ""                # 由最后事件推断
    model: str = ""
    version: str = ""               # 取首行 kind 或内置
    title: str = ""                 # 首条真实用户消息文本
    summary: str = ""               # $set.summary
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
    """工具结果。

    Gemini 的 toolCalls 内嵌 result（状态同体），
    同时 user 消息的 functionResponse 也携带结果。
    """
    tool_call_id: str
    name: str
    success: bool = True
    is_denied: bool = False
    is_error: bool = False
    error: Optional[str] = None
    output: Any = None              # 工具输出（文本或结构化）
    raw: Any = None                 # 原始 toolCalls 条目


@dataclass
class MessageItem:
    """消息内容项，对应 content 数组的一个元素。

    type 取值：text / thinking / tool_use / tool_result
    - text/thinking: 用 text 字段
    - tool_use:      用 tool_use 字段
    - tool_result:   用 tool_result 字段
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
    """单条消息（user / gemini）。

    Gemini 特殊：同一 id 可能先出现纯文本版，后出现带 toolCalls 版，
    解析时按 id 去重，保留最后出现。
    """
    id: str
    role: str                       # "user" / "assistant"（gemini 消息映射为 assistant）
    ts: int                         # 毫秒时间戳（由 ISO 转换）
    ts_iso: str
    items: List[MessageItem] = field(default_factory=list)
    model: Optional[str] = None
    usage: Optional[Usage] = None
    parent_id: Optional[str] = None # 消息树（消息 id 链）


# ──────────────────────────────────────────
# 实时状态（从屏幕快照解析）
# ──────────────────────────────────────────

@dataclass
class LiveState:
    """Gemini CLI TUI 当前实时状态，从屏幕快照解析。

    ai_status 取值：
    - idle：空闲
    - thinking：思考中（⠹ Thinking...）
    - tool_running：工具执行中
    - awaiting_approval：权限确认框（? Shell/WriteFile/Edit）
    - awaiting_answer：ask_user 对话框（Answer Questions）
    - error：API 错误（✕ [API Error]）
    - subagent_running：子代理运行中（≡ Running Agent...）
    """
    ai_status: str = "idle"
    input_text: str = ""
    mode: str = ""                  # normal / plan / auto-accept
    screen_type: str = ""           # main（欢迎页，无消息） / conversation
    model_display: str = ""
    cwd_display: str = ""
    error_count: int = 0            # 状态栏 ✖ N error
    thinking_seconds: int = 0       # 当前思考已耗时


# ──────────────────────────────────────────
# 解析结果
# ──────────────────────────────────────────

@dataclass
class ParseResult:
    """解析器最终输出。"""
    session: Session
    messages: List[Message] = field(default_factory=list)
    live_state: Optional[LiveState] = None