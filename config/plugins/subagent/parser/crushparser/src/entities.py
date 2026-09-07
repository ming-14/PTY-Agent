"""实体层：crushparser 的核心领域对象。

所有对象为纯数据结构，不依赖任何外部框架或 IO。
依赖规则：此层不导入 adapters / infra / usecases。

对齐 Crush 的数据模型（SQLite：sessions / messages / files / read_files 表）：
- roles: user / assistant / tool / system
- parts: text / reasoning / tool_call / tool_result / image_url / binary / finish / shell_command
- 时间戳单位为秒（Unix epoch），SQL 注释"milliseconds"是历史误导
- 会话 ID 为 UUID，支持 XXH3-128 hash 前缀匹配
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────
# Token / 费用相关
# ──────────────────────────────────────────

@dataclass
class Usage:
    """会话级 token 用量，对应 sessions 表的 prompt/completion_tokens / cost。"""
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost: float = 0.0


# ──────────────────────────────────────────
# 会话元数据
# ──────────────────────────────────────────

@dataclass
class Session:
    """会话元数据，来源于 crush.db 的 sessions 表。"""
    id: str
    parent_session_id: Optional[str] = None
    title: str = ""
    message_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    started_at: str = ""          # ISO 8601
    updated_at: str = ""          # ISO 8601
    summary_message_id: Optional[str] = None
    todos: List[Dict[str, Any]] = field(default_factory=list)
    data_dir: str = ""            # 来源数据库路径，用于定位


# ──────────────────────────────────────────
# 消息内容项
# ──────────────────────────────────────────

@dataclass
class ToolUse:
    """工具调用（assistant 发起）。"""
    tool_call_id: str
    name: str
    input: Dict[str, Any]
    provider_executed: bool = False
    finished: bool = True


@dataclass
class ToolResult:
    """工具结果（tool 角色消息的内容）。"""
    tool_call_id: str
    name: str
    success: bool = True
    is_error: bool = False
    is_denied: bool = False
    error: Optional[str] = None
    content: Optional[str] = None
    data: Optional[str] = None
    mime_type: Optional[str] = None
    metadata: Optional[str] = None


@dataclass
class Finish:
    """消息结束标记。"""
    reason: str = ""
    time: int = 0
    message: Optional[str] = None
    details: Optional[str] = None


@dataclass
class MessageItem:
    """消息内容项，对应 parts JSON 数组的一个元素。

    type 取值：text / thinking / tool_use / tool_result / finish / shell_command
    - text/thinking: 用 text 字段
    - tool_use:     用 tool_use 字段
    - tool_result:  用 tool_result 字段
    - finish:       用 finish 字段
    - shell_command: 用 text 字段（格式化的 command + output）
    """
    type: str
    text: Optional[str] = None
    tool_use: Optional[ToolUse] = None
    tool_result: Optional[ToolResult] = None
    finish: Optional[Finish] = None


# ──────────────────────────────────────────
# 消息
# ──────────────────────────────────────────

@dataclass
class Message:
    """单条消息（user / assistant / tool / system）。"""
    id: str
    role: str                            # user | assistant | tool | system
    ts: int                              # 秒时间戳
    ts_iso: str                          # ISO 8601
    items: List[MessageItem] = field(default_factory=list)
    model: Optional[str] = None
    provider: Optional[str] = None
    finished_at: Optional[int] = None    # 秒
    is_summary_message: bool = False


# ──────────────────────────────────────────
# 实时状态（从屏幕快照解析）
# ──────────────────────────────────────────

@dataclass
class LiveState:
    """Crush TUI 当前实时状态，从屏幕快照解析。

    ai_status 取值：
    - idle：空闲（> Ready! / Ready? / Ready for instructions）
    - thinking：思考中（> Working... 带旋转动画）
    - tool_running：工具执行中（> Processing... 或 bash 行）
    - awaiting_approval：权限请求（Permission Required 弹窗）
    - awaiting_answer：question 工具提问（等待用户选择/输入）
    """
    ai_status: str = "idle"
    input_text: str = ""
    context_percent: float = 0.0    # 侧边栏 "6% (16.5K)"
    context_tokens: int = 0         # 侧边栏 token 数
    cost_display: str = ""          # 侧边栏 "$0.00"
    screen_type: str = ""           # main（欢迎页）/ conversation（对话中）
    model_display: str = ""         # 侧边栏 "SenseNova"
    cwd_display: str = ""           # 侧边栏工作目录
    title: str = ""                 # 侧边栏会话标题
    version_display: str = ""       # "v0.90.0"


# ──────────────────────────────────────────
# 解析结果
# ──────────────────────────────────────────

@dataclass
class ParseResult:
    """解析器最终输出。"""
    session: Session
    messages: List[Message] = field(default_factory=list)
    live_state: Optional[LiveState] = None