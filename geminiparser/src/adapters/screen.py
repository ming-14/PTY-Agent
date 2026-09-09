r"""屏幕快照解析适配器：从 Gemini CLI TUI 屏幕提取实时状态（LiveState）。

Gemini CLI TUI 布局（v0.56.0，实测）：
```
 ▝▜▄     Gemini CLI v0.56.0               ← 顶部 logo + 版本
   ▝▜▄
  ▗▟▀    Authenticated with gemini-api-key /auth
[消息区（含 scrollback）]
  ✓  WriteFile  test.txt → Accepted (+1, -0)  ← 工具结果行
      1 Hello World
✦  回复文本
 ⠹ Thinking... (esc to cancel, 1m 12s)    ← AI 状态行（spinner + 计时）
 ✕ [API Error: ...]                       ← 错误行
                                                            ? for shortcuts  ← 右上角
───────────────────────────────────────────  ← 分隔线（─ 连续）
 Shift+Tab to accept edits                  ← 固定提示行
───────────────────────────────────────────
 >   Type your message or @path/to/file     ← 输入框（提示符 > + placeholder）
───────────────────────────────────────────
 workspace (/directory)  sandbox       /model  ← 状态栏（三标签）
 ~\Desktop\a             no sandbox    sensenova-6.8-flash-lite  ✖ 1 error (F12)  ← 值行
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

# ── 分隔线与固定行 ──

# 分隔线：整行几乎全为 ─（连续 10+ 个）
_SEPARATOR_RE = re.compile(r"^\s*─{10,}\s*$")

# 输入框：以 > 开头（提示符后跟文本）
_INPUT_RE = re.compile(r"^>\s?(.*)$")

# 模式指示（分隔线旁）
_MODE_PLAN_RE = re.compile(r"plan\s+Shift\+Tab")
_MODE_AUTO_ACCEPT_RE = re.compile(r"auto-accept\s+edits")

# ── AI 状态检测 ──

# 思考中旋转动画（Braille）
_SPINNER_CHARS = frozenset("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

# 思考中
_THINKING_RE = re.compile(r"Thinking\s*\.\.\.\s*\(esc to cancel")
_THINKING_TIMER_RE = re.compile(r"\(esc to cancel,?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?\)")

# 工具执行中
_TOOL_RUNNING_KEYWORDS = ("esc to interrupt",)

# 提问对话框
_ANSWER_QUESTIONS_RE = re.compile(r"Answer Questions")
_ASK_SELECT_RE = re.compile(r"Enter to select.*↑/↓.*Esc to cancel")

# 权限确认
_PERMISSION_RE = re.compile(r"(?:Allow execution of|Apply this change|Do you want to proceed)")
_ALLOW_OPTIONS = ("Allow once", "Allow for this session", "Allow for this session")

# 错误
_ERROR_RE = re.compile(r"✕\s*\[API Error")
_ERROR_COUNT_RE = re.compile(r"✖\s*(\d+)\s*(?:error|errors)")

# 子代理
_SUBAGENT_RUNNING_RE = re.compile(r"≡ Running Agent")
_SUBAGENT_COMPLETED_RE = re.compile(r"≡ Agent Completed")

# 模型选择
_MODEL_DIALOG_RE = re.compile(r"Select Model")

# 计划确认
_PLAN_CONFIRM_RE = re.compile(r"Ready to start implementation")

# 反向搜索
_REVERSE_SEARCH_RE = re.compile(r"^\(r:\)")

# 欢迎页标志
_WELCOME_LOGO = "▝▜▄"

# 状态栏标签
_STATUSBAR_MODEL_RE = re.compile(r"/model\s*$")
_STATUSBAR_WORKSPACE_RE = re.compile(r"workspace\s*\(/directory\)")


def _detect_ai_status(lines: List[str]) -> str:
    """从消息区检测 AI 状态。

    Returns:
        idle / thinking / tool_running / awaiting_answer /
        awaiting_approval / error / subagent_running
    """
    scan_text = "\n".join(lines)

    # 子代理（先于其他判定）
    if _SUBAGENT_RUNNING_RE.search(scan_text):
        return "subagent_running"

    # 错误
    if _ERROR_RE.search(scan_text):
        return "error"

    # 思考中
    if _THINKING_RE.search(scan_text):
        return "thinking"

    # 工具执行中
    for kw in _TOOL_RUNNING_KEYWORDS:
        if kw in scan_text:
            return "tool_running"

    # 旋转动画（东西在跑）
    if any(ch in scan_text for ch in _SPINNER_CHARS):
        return "tool_running"

    # 提问对话框
    if _ANSWER_QUESTIONS_RE.search(scan_text) and _ASK_SELECT_RE.search(scan_text):
        return "awaiting_answer"

    # 权限确认
    if _PERMISSION_RE.search(scan_text):
        return "awaiting_approval"

    # 权限确认（允许选项列表）
    for opt in _ALLOW_OPTIONS:
        if opt in scan_text:
            return "awaiting_approval"

    return "idle"


def _extract_thinking_seconds(text: str) -> int:
    """从 Thinking... 行提取已耗秒数。

    格式：(esc to cancel, 5s) / (esc to cancel, 1m 12s) / (esc to cancel, 1m)
    """
    m = _THINKING_TIMER_RE.search(text)
    if m:
        minutes = int(m.group(1)) if m.group(1) else 0
        seconds = int(m.group(2)) if m.group(2) else 0
        return minutes * 60 + seconds
    return 0


def _extract_error_count(text: str) -> int:
    """从状态栏提取错误计数。"""
    m = _ERROR_COUNT_RE.search(text)
    if m:
        return int(m.group(1))
    return 0


def parse_screen_lines(lines: List[str]) -> LiveState:
    """从已解析的屏幕文本行提取 LiveState。

    解析顺序：
    1. 检测屏幕类型（main / conversation）
    2. 从底部往上定位输入框与状态栏
    3. 提取各字段
    """
    state = LiveState()

    if not lines:
        return state

    # 检测屏幕类型
    has_logo = any(_WELCOME_LOGO in line for line in lines)
    # 定位分隔线位置，确定消息区（分隔线之前）和输入框
    sep_positions = [i for i, line in enumerate(lines) if _SEPARATOR_RE.match(line)]
    # 消息区：第一条分隔线之前；无分隔线时检查全部屏幕
    msg_area_end = sep_positions[0] if sep_positions else len(lines)
    msg_area = lines[:msg_area_end]
    has_messages = any("✦" in line for line in msg_area) or any(
        line.strip().startswith("> ") and "Type your message" not in line
        for line in msg_area
    )

    if has_logo and not has_messages:
        state.screen_type = "main"
    else:
        state.screen_type = "conversation"

    # 状态栏在最后一条分隔线之后
    statusbar_start = sep_positions[-1] + 1 if sep_positions else max(0, len(lines) - 4)

    # 输入框区域：最后一条分隔线之前
    input_area = lines[:sep_positions[-1]] if sep_positions else lines

    # 模式检测（分隔线旁）
    if sep_positions:
        sep_line = lines[sep_positions[-1]]
        if _MODE_AUTO_ACCEPT_RE.search(sep_line):
            state.mode = "auto-accept"
        elif _MODE_PLAN_RE.search(sep_line):
            state.mode = "plan"

    # 输入框文本：从输入区域底部找 > 行
    for line in reversed(input_area):
        m = _INPUT_RE.match(line)
        if m:
            text = m.group(1).strip()
            if text and text != "Type your message or @path/to/file":
                state.input_text = text
            break

    # 状态栏：模型名 / 工作目录
    if sep_positions and statusbar_start < len(lines):
        statusbar_lines = [l.strip() for l in lines[statusbar_start:] if l.strip()]
        if statusbar_lines:
            status_text = " ".join(statusbar_lines)
            # 移除对话框边框字符（╭╮╰╯│）和错误计数
            status_text = status_text.replace("╭", "").replace("╮", "").replace("╰", "").replace("╯", "").replace("│", "")
            error_m = _ERROR_COUNT_RE.search(status_text)
            if error_m:
                state.error_count = int(error_m.group(1))
                status_text = status_text[:error_m.start()].strip()
            tokens = status_text.split()
            # 模型名：状态栏最后一个 token（值行最右侧）
            if tokens:
                state.model_display = tokens[-1]
            # 工作目录：匹配路径形态的 token（~\... 或 C:\...）
            for t in tokens:
                if t.startswith("~") or ("\\" in t) or t.startswith("C:"):
                    state.cwd_display = t
                    break

    # AI 状态
    state.ai_status = _detect_ai_status(lines)

    # 思考计时
    if state.ai_status == "thinking":
        state.thinking_seconds = _extract_thinking_seconds("\n".join(lines))

    _log.debug("parse_screen: status=%s mode=%s input=%r screen=%s model=%s",
               state.ai_status, state.mode, state.input_text,
               state.screen_type, state.model_display)
    return state


def parse_screen_snapshot(vt_text: str) -> LiveState:
    """从带 VT 序列的屏幕全量文本提取 LiveState。

    Args:
        vt_text: 纯 VT 输出（不含 PTY-Agent 元数据 header/footer）
                 推荐用 PTY-Agent -o 输出到文件获取干净内容

    Returns:
        LiveState 实体
    """
    lines = parse_screen(vt_text, rstrip=False)
    return parse_screen_lines(lines)