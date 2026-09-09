"""屏幕快照解析适配器：从 Goose session TUI 屏幕提取实时状态（LiveState）。

Goose session TUI 布局（v1.47.0，实测）：:

    __( O)>  ● new session · openai sensenova-6.8-flash-lite   <- 标题行：logo + 会话标题 + provider/model
   \\____)    20260823_3 · C:\\Users\\<user>\\Desktop\\pty-agent  <- 会话 ID + 工作目录
     L L     goose is ready                                     <- 状态提示
  ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ 0% 0/128k                               <- 上下文进度条
> Enter to send · Ctrl+J newline                                <- 输入提示（placeholder）

对话中：:

    > 请用 Python 写一个贪吃蛇游戏                             <- 用户消息回显
      ────────────────────────────────────────                  <- 分隔线
      ▸ todo_write todo                                         <- 工具调用行
        content: - [ ] 编写...                                   <- 工具参数
    ✅ 贪吃蛇游戏已保存到 ...                                    <- AI 回复
      ⏱ 32.41s                                                  <- 回合耗时
      ━━╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ 9% 12k/128k                         <- 进度条（已用）
    > Enter to send · Ctrl+J newline                             <- 输入提示

解析策略：标题行定位 logo 与工作目录；底部定位输入提示与进度条。
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..entities import LiveState
from ..infra.logging import get_logger
from ..infra.vt import parse_screen

_log = get_logger("screen")

# ── 分段正则 ──

# 标题行：logo + 会话标题 + provider/model
#   "    __( O)>  ● new session · openai sensenova-6.8-flash-lite"
_HEADER_RE = re.compile(r"\(\s*O\s*\)\s*>\s*(.*)$", re.MULTILINE)

# 会话信息行：会话 ID + 工作目录
#   "   \____)    20260823_3 · C:\Users\<user>\Desktop\pty-agent"
_SESSION_LINE_RE = re.compile(r"\)\s*(\S+)\s*·\s*(.*)$", re.MULTILINE)

# 输入提示（placeholder）：> Enter to send · Ctrl+J newline
_INPUT_PLACEHOLDER = "Enter to send"

# 输入框：以 > 开头（用户输入回显或输入框）
_INPUT_RE = re.compile(r"^>\s?(.*)$")

# 上下文进度条：百分比 + used/limit
#   "  ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ 0% 0/128k"
#   "  ━━╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ 9% 12k/128k"
_PROGRESS_RE = re.compile(r"([━╌═─]+)\s*(\d+(?:\.\d+)?)%\s*([\d.]+[kKmM]?)/([\d.]+[kKmM]?)")

# 回合耗时：  ⏱ 32.41s
_ELAPSED_RE = re.compile(r"⏱\s*([\d.]+)s")

# 工具调用行：  ▸ tool_name
_TOOL_RE = re.compile(r"^\s*▸\s+(\S+)")

# 状态提示行："goose is ready" / "thinking..." 等
_STATUS_RE = re.compile(r"^\s*(goose is ready|.*thinking.*|.*working.*|.*waiting.*)\s*$", re.IGNORECASE)

# 模型名（标题行中 provider 后的部分）
_MODEL_DISPLAY_RE = re.compile(r"(?:openai|anthropic|google|ollama|openrouter|azure|bedrock|sensenova)\s+(\S+)", re.IGNORECASE)


def _parse_context_percent(text: str) -> Optional[float]:
    """从进度条文本提取上下文占用百分比。"""
    m = _PROGRESS_RE.search(text)
    if m:
        try:
            return float(m.group(2))
        except ValueError:
            return None
    return None


def _detect_ai_status(lines: List[str]) -> str:
    """从屏幕检测 AI 状态。

    Returns:
        idle / thinking / tool_running / awaiting_approval
    """
    joined = "\n".join(lines)

    # 输入提示存在 → 空闲（等待输入）
    if _INPUT_PLACEHOLDER in joined:
        # 但如果有工具调用行且无完成标记，可能是工具执行中
        tool_lines = [l for l in lines if _TOOL_RE.search(l)]
        if tool_lines and "⏱" not in joined:
            # 最后一行是工具行且没有耗时 → 工具仍在执行
            last_nonempty = next((l for l in reversed(lines) if l.strip()), "")
            if last_nonempty.strip() and _TOOL_RE.search(last_nonempty):
                return "tool_running"
        return "idle"

    # 无输入提示 → 工作中
    # 工具调用行 + 无完成 → tool_running
    if any(_TOOL_RE.search(l) for l in lines):
        return "tool_running"

    # 权限/确认请求
    if any(kw in joined for kw in ("Do you want to", "proceed?", "Approve", "confirm")):
        return "awaiting_approval"

    if "thinking" in joined.lower() or "working" in joined.lower():
        return "thinking"

    return "tool_running"


def _reassemble_header_text(lines: List[str]) -> str:
    """从可能 wrap 的多行中重拼接标题行完整文本。

    Goose TUI 在窄屏（40 列）下标题行会 wrap 成两行：
        [1] '    __( O)>  ● new session · openai sens'
        [2] 'enova-6.8-flash-lite'
    返回拼接后的完整字符串。
    """
    for i, line in enumerate(lines):
        m = _HEADER_RE.search(line)
        if not m:
            continue
        # 找到 logo 行，收集后续续行
        parts = [line.rstrip()]
        for j in range(i + 1, len(lines)):
            nxt = lines[j].rstrip()
            if not nxt:
                break
            # 续行终止条件：遇到会话行、状态行、用户输入行
            if _SESSION_LINE_RE.search(nxt) or "L L" in nxt or _INPUT_RE.match(nxt) or _PROGRESS_RE.search(nxt):
                break
            parts.append(nxt)
        return "".join(parts)
    return ""


def _reassemble_session_text(lines: List[str]) -> str:
    """从可能 wrap 的多行中重拼接会话信息行完整文本。

    Goose TUI 在窄屏下会话行会 wrap 成两行：
        [3] '   \\____)    20260823_5 · C:\\Users\\<user>'
        [4] '\\Desktop\\pty-agent'
    """
    for i, line in enumerate(lines):
        m = _SESSION_LINE_RE.search(line)
        if not m:
            continue
        parts = [line.rstrip()]
        for j in range(i + 1, len(lines)):
            nxt = lines[j].rstrip()
            if not nxt:
                break
            if "L L" in nxt or _HEADER_RE.search(nxt) or _INPUT_RE.match(nxt) or _PROGRESS_RE.search(nxt):
                break
            parts.append(nxt)
        return "".join(parts)
    return ""


def _extract_cwd_display(lines: List[str]) -> str:
    """从会话信息行提取工作目录显示。"""
    session_text = _reassemble_session_text(lines)
    if session_text:
        m = _SESSION_LINE_RE.search(session_text)
        if m:
            return m.group(2).strip()
    return ""


def _extract_session_title(lines: List[str]) -> str:
    """从标题行提取会话标题。"""
    header_text = _reassemble_header_text(lines)
    if header_text:
        m = _HEADER_RE.search(header_text)
        if m:
            title = m.group(1).strip()
            title = title.lstrip("●").strip()
            title = re.split(r"\s·\s", title)[0].strip()
            return title
    return ""


def _extract_model_display(lines: List[str]) -> str:
    """从标题行提取模型显示名。"""
    header_text = _reassemble_header_text(lines)
    if header_text:
        m = _HEADER_RE.search(header_text)
        if m:
            title = m.group(1)
            mm = _MODEL_DISPLAY_RE.search(title)
            if mm:
                return mm.group(1)
            return title.lstrip("●").strip().split()[0] if title.strip() else ""
    return ""


def _extract_elapsed(lines: List[str]) -> Optional[float]:
    """从屏幕提取回合耗时（秒）。"""
    for line in lines:
        m = _ELAPSED_RE.search(line)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


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

    # 界面类型：标题行 logo 存在 = main（欢迎页）；有对话内容 = conversation
    # 用重拼接后的标题行判断（窄屏 wrap 时原行可能只含 logo 前缀）
    has_logo = _HEADER_RE.search(_reassemble_header_text(lines)) is not None
    # 真实对话标记：用户输入回显行（非 placeholder）、工具调用、AI 回复
    user_input_lines = [
        l for l in lines
        if _INPUT_RE.match(l) and "Enter to send" not in l
    ]
    has_conversation_mark = (
        bool(user_input_lines)
        or any(_TOOL_RE.search(l) for l in lines)
        or _ELAPSED_RE.search(joined) is not None
    )
    state.screen_type = "main" if (has_logo and not has_conversation_mark) else "conversation"

    # 输入框文字：> 后的文本（排除 placeholder 行）
    input_text = ""
    for line in lines:
        m = _INPUT_RE.match(line)
        if m:
            text = m.group(1).strip()
            if text and "Enter to send" not in text:
                input_text = text
                break
    state.input_text = input_text

    # 上下文百分比
    for line in lines:
        pct = _parse_context_percent(line)
        if pct is not None:
            state.context_percent = pct
            break

    # 模型 / 工作目录 / 会话标题 / 耗时
    state.model_display = _extract_model_display(lines)
    state.cwd_display = _extract_cwd_display(lines)
    state.session_title = _extract_session_title(lines)
    state.elapsed_seconds = _extract_elapsed(lines)

    # AI 状态
    state.ai_status = _detect_ai_status(lines)

    _log.debug("parse_screen_lines: status=%s input=%r pct=%s screen=%s model=%s",
               state.ai_status, state.input_text, state.context_percent,
               state.screen_type, state.model_display)
    return state


def parse_screen_snapshot(vt_text: str) -> LiveState:
    """从带 VT 序列的屏幕全量文本提取 LiveState。

    Args:
        vt_text: 纯 VT 输出（PTY-Agent read --keep-ansi 输出）

    Returns:
        LiveState 实体
    """
    lines = parse_screen(vt_text, rstrip=False)
    return parse_screen_lines(lines)