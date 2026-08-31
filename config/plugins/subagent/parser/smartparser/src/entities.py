"""实体层：smartparser 的核心领域对象。

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
    """会话级 token 用量（smartagent 无 API 调用，占位兼容）"""
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost: float = 0.0


# ──────────────────────────────────────────
# 会话元数据
# ──────────────────────────────────────────

@dataclass
class Session:
    """会话元数据，来自 JSONL 首条事件或 --sid/--prompt 参数。"""
    id: str
    title: str = ""
    role: str = ""              # --model 映射的角色/视角
    started_at: str = ""
    status: str = ""
    usage: Usage = field(default_factory=Usage)


# ──────────────────────────────────────────
# 消息内容项
# ──────────────────────────────────────────

@dataclass
class MessageItem:
    """消息内容项（smartagent 仅 text 类型）。"""
    type: str                    # "text"
    text: Optional[str] = None


# ──────────────────────────────────────────
# 消息
# ──────────────────────────────────────────

@dataclass
class Message:
    """单条消息。

    role 语义：user = AI 发来的消息（AI 视角的"用户"），
    assistant = 人类回复（子代理视角的"助手"）。
    与现有 parser 对齐，使 read --rf message 显示一致。
    """
    id: str
    role: str                    # "user"（AI 消息）| "assistant"（人类回复）
    ts: int                      # 毫秒时间戳
    ts_iso: str
    items: List[MessageItem] = field(default_factory=list)
    usage: Optional[Usage] = None


# ──────────────────────────────────────────
# 实时状态（从 AI 屏幕文本解析）
# ──────────────────────────────────────────

@dataclass
class LiveState:
    """smartagent.py 渲染的 AI 屏幕实时状态，从屏幕文本解析。

    ai_status 取值：
    - idle：等待人类输入
    - input：人类正在输入
    - sent：人类已提交回复
    - thinking：/tool_/thinking 等占位（smartagent 无，保留兼容）
    - tool_running：同上
    """
    ai_status: str = "idle"
    input_text: str = ""
    screen_type: str = "conversation"  # 固定为 conversation
    status_bar: str = ""               # 状态栏原文


# ──────────────────────────────────────────
# 解析结果
# ──────────────────────────────────────────

@dataclass
class ParseResult:
    """解析器最终输出。"""
    session: Session
    messages: List[Message] = field(default_factory=list)
    live_state: Optional[LiveState] = None