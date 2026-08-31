"""实体层：claudeparser 的核心领域对象。

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

    字段命名对齐 example-project 的 Usage/Metrics 语义：
    - input/output：常规输入输出
    - cache_read / cache_creation：提示缓存命中与写入
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    total_cost: float = 0.0


# ──────────────────────────────────────────
# 会话元数据
# ──────────────────────────────────────────

@dataclass
class Session:
    """会话元数据，聚合自 ~/.claude/sessions/<pid>.json 与 jsonl 事件。"""
    id: str
    cwd: str = ""
    started_at: str = ""
    status: str = ""
    model: str = ""
    version: str = ""
    mode: str = ""                  # normal / plan / auto 等
    permission_mode: str = ""       # default / acceptEdits 等
    git_branch: str = ""
    pid: Optional[int] = None
    entrypoint: str = ""
    name: str = ""
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
class ToolResultEntry:
    """工具结果中的单条输出（list 形态 content 元素，cline 兼容）。"""
    query: str = ""
    output: str = ""
    success: bool = True
    error: Optional[str] = None


@dataclass
class ToolResult:
    """工具结果（user 返回）。

    claude 的 tool_result 为字符串 content；cline 为 list/str 双形态，
    这里统一：
    - result: 原始内容（字符串或 list）
    - entries: list 形态时拆分出的条目
    """
    tool_call_id: str
    name: str
    success: bool = True
    is_denied: bool = False
    is_error: bool = False
    error: Optional[str] = None
    result: Any = None
    entries: List[ToolResultEntry] = field(default_factory=list)


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
    """单条消息（user / assistant 事件）。"""
    id: str
    role: str
    ts: int                      # 毫秒时间戳（由 ISO 转换）
    ts_iso: str
    items: List[MessageItem] = field(default_factory=list)
    model: Optional[str] = None
    effort: Optional[str] = None
    prompt_source: Optional[str] = None
    usage: Optional[Usage] = None


# ──────────────────────────────────────────
# 实时状态（从屏幕快照解析）
# ──────────────────────────────────────────

@dataclass
class LiveState:
    """Claude Code TUI 当前实时状态，从屏幕快照解析。"""
    ai_status: str = "idle"       # idle / thinking / tool_running / awaiting_approval / awaiting_answer
    input_text: str = ""
    effort: str = ""              # high / medium / low
    permission_mode: str = ""     # 状态栏左侧，如 "manual mode on"
    mode: str = ""                # normal / plan
    screen_type: str = ""         # main（欢迎页） / conversation
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
