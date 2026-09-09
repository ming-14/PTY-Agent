"""屏幕快照解析适配器：从 codex TUI 屏幕提取实时状态（LiveState）。

codex TUI 布局（v0.80.0，实测）：
```
╭─ update banner ─╮（可有可无）
╭─ header: >_ OpenAI Codex / model: ... / directory: ... ─╮（仅欢迎页）
[消息区]
› <用户输入>              ← 用户消息回显
• <AI回复>                ← 助手回复
• Ran <command>           ← 工具执行
  └ <输出>                ← 工具输出
─ Worked for Ns ─...      ← 回合分隔线
◦ Working (Ns • esc to interrupt)  ← 工作中
[权限请求框]
  Would you like to run the following command?
  ...
› 1. Yes / 2. Yes and don't ask / 3. No (esc)
  Press enter to confirm or esc to cancel
[拒绝]
✗ You canceled the request...
  └ exec command rejected by user
■ Conversation interrupted...
[输入框]
› Implement {feature}     ← placeholder
[状态栏]
  100% context left · ? for shortcuts
```

解析策略：从底部往上定位输入框与状态栏，分段正则提取各字段。
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..entities import LiveState
from ..infra.logging import get_logger
from ..infra.vt import parse_screen

_log = get_logger("screen")

# ── 正则 ──

# 输入框：› 后文本（排除权限选项行）
_INPUT_RE = re.compile(r"›\s?(.*)")

# 权限选项行：› 1. Yes ... / › 2. ...（非输入框）
_OPTION_RE = re.compile(r"›\s*\d+\.")

# 状态栏：context left
_CONTEXT_RE = re.compile(r"(\d+)\s*%\s*context\s*left")

# 工作中：Working + esc to interrupt
_WORKING_RE = re.compile(r"(?:[◦•])\s*Working\s*\((\d+)s")

# 工具执行中（进行时）
_TOOL_RUNNING_RE = re.compile(r"•\s*Running\s+")

# 对话框关键词（等待用户确认的模态对话框）
_DIALOG_KEYWORDS = (
    "Press enter to confirm or esc to cancel",
    "Press enter to confirm or esc to go back",
    "Press enter to select",
    "Would you like to run the following command?",
)

# 拒绝/中断关键词
_DENIED_RE = re.compile(r"✗\s*You canceled")

# 欢迎页特征
_WELCOME_HEADER = ">_ OpenAI Codex"

# 对话标记
_CONVERSATION_MARKS = (
    "Worked for",
    "• Ran",
    "• Running",
    "Conversation interrupted",
)

# placeholder 文本
_PLACEHOLDER = "Implement {feature}"


def _detect_ai_status(lines: List[str]) -> str:
    """从消息区和底部检测 AI 状态。

    Returns:
        idle / thinking / tool_running / awaiting_approval
    """
    scan_text = "\n".join(lines)

    for kw in _DIALOG_KEYWORDS:
        if kw in scan_text:
            return "awaiting_approval"

    if _WORKING_RE.search(scan_text):
        # 工具执行中（进行时 "• Running"）vs 思考中（仅 Working）
        if _TOOL_RUNNING_RE.search(scan_text):
            return "tool_running"
        return "thinking"

    return "idle"


def _find_input_text(lines: List[str]) -> str:
    """从底部往上找输入框行，提取输入文本。

    权限请求框出现时输入框不可编辑，返回空字符串。
    """
    # 对话框（权限/模型/推理等级）时输入框不可编辑
    joined = "\n".join(lines)
    for kw in _DIALOG_KEYWORDS:
        if kw in joined:
            return ""

    # 状态栏行（含 "context left"）之上的第一个 › 行
    context_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        if _CONTEXT_RE.search(lines[i]):
            context_idx = i
            break
    if context_idx < 0:
        context_idx = len(lines)

    # 从 context_idx 往上找最后一个 › 行（跳过权限选项行）
    for i in range(context_idx - 1, -1, -1):
        line = lines[i]
        if _OPTION_RE.match(line):
            continue
        m = _INPUT_RE.match(line)
        if m:
            text = m.group(1).strip()
            # 排除 placeholder
            if text == _PLACEHOLDER:
                return ""
            return text
    return ""


def _extract_context_percent(lines: List[str]) -> float:
    """从状态栏提取上下文百分比。"""
    for line in lines:
        m = _CONTEXT_RE.search(line)
        if m:
            return float(m.group(1))
    return 0.0


def parse_screen_lines(lines: List[str]) -> LiveState:
    """从已解析的屏幕行列表提取 LiveState。

    Args:
        lines: 屏幕各行文本（pyte 解析后）

    Returns:
        LiveState 实体
    """
    state = LiveState()

    if not lines:
        return state

    joined = "\n".join(lines)

    # 界面类型
    has_welcome = _WELCOME_HEADER in joined
    has_conversation = any(mark in joined for mark in _CONVERSATION_MARKS) \
        or bool(_WORKING_RE.search(joined))
    state.screen_type = "main" if (has_welcome and not has_conversation) else "conversation"

    # 输入框文字
    state.input_text = _find_input_text(lines)

    # 上下文百分比
    state.context_percent = _extract_context_percent(lines)

    # AI 状态
    state.ai_status = _detect_ai_status(lines)

    # 模型名 / 工作目录（从欢迎页 header，剥离框线字符）
    for line in lines:
        s = line.strip().strip("│╭╮╰╯").strip()
        if "model:" in s and "/model to change" in s:
            m = re.search(r"model:\s*(.+?)\s*/model", s)
            if m:
                state.model_display = m.group(1).strip()
        elif "directory:" in s:
            m = re.search(r"directory:\s*(.+)", s)
            if m:
                state.cwd_display = m.group(1).strip()

    _log.debug("parse_screen_lines: status=%s ctx=%s%% input=%r screen=%s",
               state.ai_status, state.context_percent, state.input_text, state.screen_type)
    return state


def parse_screen_snapshot(vt_text: str) -> LiveState:
    """从带 VT 序列的屏幕全量文本提取 LiveState。

    Args:
        vt_text: PTY-Agent --keep-ansi 输出

    Returns:
        LiveState 实体
    """
    lines = parse_screen(vt_text, rstrip=False)
    return parse_screen_lines(lines)