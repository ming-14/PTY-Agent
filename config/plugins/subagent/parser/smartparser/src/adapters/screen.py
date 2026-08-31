"""屏幕快照解析适配器：从 smartagent.py 渲染的 AI 屏幕文本提取实时状态（LiveState）。

smartagent.py 的 stdout 就是 AI 屏幕（read --rf snapshot 看到），格式：
```
────────────────────────────────────────────
 Smart Chat — <sid>
────────────────────────────────────────────
[You] 帮我检查 README
[Smart] 好的我看一下
────────────────────────────────────────────
Smart工作中…                          ← 状态栏
────────────────────────────────────────────
```
状态语义（与 TurnMonitor busy/idle 对齐）：
- "idle（等待 AI 消息）"   → idle（等 AI 派发，不算回合）
- "Smart工作中…"          → tool_running（AI 已派发，人类工作中 = busy）
- "Smart已回复"           → idle（人类提交 = 回合完成信号）
"""
from __future__ import annotations

import re
from typing import List

from ..entities import LiveState
from ..infra.logging import get_logger

_log = get_logger("screen")

_IDLE_RE = re.compile(r"idle（等待 AI 消息）|^idle$")
_WORKING_RE = re.compile(r"Smart工作中…|^working$|^tool_running$")
_SENT_RE = re.compile(r"Smart已回复|^sent$")
_DROPPED_RE = re.compile(r"Smart Agent 已砸锅")


def parse_screen_snapshot(text: str) -> LiveState:
    """从带 VT 序列的 AI 屏幕文本提取 LiveState。"""
    state = LiveState()
    if not text:
        return state

    clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    lines = clean.splitlines()

    state.screen_type = "conversation"

    # 解析状态栏：最后几个非空行之一
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("─"):
            continue
        if stripped.startswith("Smart Chat"):
            continue
        state.status_bar = stripped
        if _WORKING_RE.search(stripped):
            state.ai_status = "tool_running"  # 人类工作中（busy）
        elif _SENT_RE.search(stripped):
            state.ai_status = "idle"  # 人类已提交 = 回合完成
        elif _DROPPED_RE.search(stripped):
            state.ai_status = "idle"  # 已砸锅 = 回合完成（异常完成）
        else:
            state.ai_status = "idle"
        break

    _log.debug("parse_screen: ai_status=%s", state.ai_status)
    return state