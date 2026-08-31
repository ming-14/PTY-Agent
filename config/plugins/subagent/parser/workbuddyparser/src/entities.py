"""实体层：workbuddyparser 的核心领域对象。

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

    字段对齐 CodeBuddy Code 的 rawUsage 语义：
    - input_tokens  = prompt_tokens
    - output_tokens = completion_tokens
    """
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0


# ──────────────────────────────────────────
# 会话元数据
# ──────────────────────────────────────────

@dataclass
class Session:
    """会话元数据，聚合自 ~/.workbuddy/sessions/<pid>.json、
    workbuddy.db 与 jsonl 事件。"""
    id: str
    cwd: str = ""
    started_at: str = ""            # 毫秒时间戳字符串或 ISO
    status: str = ""                # completed / archived / ...
    model: str = ""
    model_provider: str = ""
    cli_version: str = ""
    mode: str = ""                  # craft 等
    permission_mode: str = ""       # fullAccess / acceptEdits / default / ...
    source_mode: str = ""           # coding
    title: str = ""                 # ai-title 事件 / workbuddy.db
    pid: Optional[int] = None
    usage: Usage = field(default_factory=Usage)


# ──────────────────────────────────────────
# 消息内容项
# ──────────────────────────────────────────

@dataclass
class ToolUse:
    """工具调用（function_call 事件）。"""
    tool_call_id: str
    name: str
    input: Dict[str, Any]


@dataclass
class ToolResult:
    """工具结果（function_call_result 事件）。

    CodeBuddy 的 output 为 dict：{"type": "text", "text": "Command: ...\nStdout: ..."}。
    解析时提取 output.text 并识别 exit_code / is_error / is_denied。
    """
    tool_call_id: str
    name: str
    success: bool = True
    is_denied: bool = False
    is_error: bool = False
    error: Optional[str] = None
    output: Any = None              # 原始 output dict 或字符串
    output_text: str = ""
    exit_code: Optional[int] = None


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
    """单条消息（message 事件，user / assistant）。"""
    id: str
    role: str
    ts: int                          # 毫秒时间戳
    ts_iso: str                      # ISO 字符串（CodeBuddy 为毫秒 int，转 ISO 或留空）
    items: List[MessageItem] = field(default_factory=list)
    model: Optional[str] = None
    usage: Optional[Usage] = None
    status: Optional[str] = None     # completed / incomplete
    prompt_source: Optional[str] = None


# ──────────────────────────────────────────
# 实时状态（从屏幕快照解析）
# ──────────────────────────────────────────

@dataclass
class LiveState:
    """CodeBuddy Code TUI 当前实时状态，从屏幕快照解析。"""
    ai_status: str = "idle"          # idle / thinking / tool_running / awaiting_approval / asking / unknown
    input_text: str = ""
    screen_type: str = ""            # main（欢迎页） / conversation
    model_display: str = ""
    cwd_display: str = ""
    thinking_on: str = ""            # Thinking on (AltT to toggle)
    permission_mode: str = ""        # bypass permissions on / manual mode on / auto-accept ...
    mode: str = ""                   # 模式（如 craft）


# ──────────────────────────────────────────
# 解析结果
# ──────────────────────────────────────────

@dataclass
class ParseResult:
    """解析器最终输出。"""
    session: Session
    messages: List[Message] = field(default_factory=list)
    live_state: Optional[LiveState] = None
