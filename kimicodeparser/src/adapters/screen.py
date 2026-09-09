r"""屏幕快照解析适配器：从 Kimi Code TUI 屏幕提取实时状态（LiveState）。

Kimi Code TUI 布局（v0.38.0，实测 7 种状态）：
```
╭──────────────────────────────────────────────────╮   ← 欢迎页框（蓝色）
│  ▐█▛█▛█▌  Welcome to Kimi Code!                   │
│  ▐█████▌  Send /help for help information.         │
│  Directory: C:\Users\<user>\Desktop\kimicodeparser  │
│  Session:   session_<UUID>                         │
│  Model:     sensenova-6.8-flash-lite               │
│  Version:   0.38.0                                 │
╰──────────────────────────────────────────────────╯

 ✨ 用户消息
 ● assistant 思考或回复正文
   ⠋ working... · Tip: ...
   ✗ Ran a command / $ command / Approved: / Rejected:

   ▶ Run this command?                    ← 批准对话框
   cwd: C:/Users/...
   $ command
   ▶ 1. Approve once
     2. Approve for this session
     3. Reject
     4. Reject with feedback
   ↑/↓ select · 1/2/3/4 choose · ↵ confirm

╭──────────────────────────────────────────────────╮
│ > 输入框文本                                       │
╰──────────────────────────────────────────────────╯
 sensenova-6.8-flash-lite thinking  C:\...  <tip>  context: 10% (23.3k/256k)
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

# ── 分段正则 ──

# 分隔线：整行几乎全为 ─（消息区/输入框分隔）
_SEPARATOR_RE = re.compile(r"^\s*(?:[╭╰╮╯]?\s*─{10,}\s*[╮╯]?)\s*$")

# 输入框：以 > 开头（可能被 │ 框包裹）
_INPUT_RE = re.compile(r"^[│╰]?\s*>\s?(.*)$")

# 状态栏 context 百分比
_CONTEXT_RE = re.compile(r"context:\s*(\d+)%")

# 欢迎页特征
_WELCOME_HEADER = "Welcome to Kimi Code!"
_WELCOME_FRAME_TOP = "╭──"

# 对话内容标记（不含 ✦，因为欢迎页也有 ✦ Try Kimi Code Web UI）
_CONVERSATION_MARKS = (
    "● ",
    "⠋",
    "✨",
    "✗ ",
    "▶ Run this command?",
    "Ran a command",
    "Running a command",
)

# Braille spinner 字符集（工作中）
_SPINNER_CHARS = frozenset("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

# 月亮图标（空闲）
_MOON_CHARS = frozenset("🌑🌘🌗🌖🌕")

# 权限请求/批准对话框关键词
_APPROVAL_PROMPT = "▶ Run this command?"
_APPROVAL_SELECT = "↑/↓ select · 1/2/3/4 choose · ↵ confirm"

# 输入框 placeholder
_PLACEHOLDER_MARK = "↵ send"


def _detect_ai_status(lines: List[str]) -> str:
    """从消息区检测 AI 状态。

    检测顺序：awaiting_approval → asking → working → idle

    Kimi 各种状态的独特标记：
    - awaiting_approval：`▶ Run this command?` + 选项列表
    - asking：`Ready to submit your answers?` / `↑↓ select` + `choose`
    - working：Braille spinner（⠋）/ `● Running a command`
    - idle：月亮图标（🌑）或以上皆无
    """
    scan_text = "\n".join(lines)

    # 1. 批准对话框（awaiting_approval）最高优先级
    if _APPROVAL_PROMPT in scan_text:
        return "awaiting_approval"
    if _APPROVAL_SELECT in scan_text:
        return "awaiting_approval"

    # 2. AI 提问对话框（asking）— 优先于 spinner（历史消息中可能有 ⠋ 残留）
    if "Ready to submit your answers?" in scan_text:
        return "asking"
    if "↑↓ select" in scan_text and "choose" in scan_text:
        return "asking"
    # 兼容其他 CLI 的 ask 格式
    if "Enter to select · ↑/↓ to navigate · Esc to cancel" in scan_text:
        return "asking"
    if "↑/↓ to navigate" in scan_text and "Enter to select" in scan_text:
        return "asking"

    # 3. Braille spinner → working
    for ch in _SPINNER_CHARS:
        if ch in scan_text:
            # spinner 后跟 working/thinking
            if "working" in scan_text or "thinking" in scan_text or "Thinking" in scan_text:
                return "working"
            # 纯 spinner 也视为 working
            return "working"

    # ✗ Ran a command 或 ● Running a command → 工具执行中
    if "● Running a command" in scan_text or "● Ran a command" in scan_text:
        return "tool_running"
    if "✗ Ran a command" in scan_text or "✗ Used" in scan_text:
        return "tool_running"

    # ● 前缀行包含 think 文本 → 思考中
    if "Thinking" in scan_text or "∴ " in scan_text:
        return "working"

    # 月亮图标 → idle
    for ch in _MOON_CHARS:
        if ch in scan_text:
            return "idle"

    return "idle"


def _find_input_and_status(lines: List[str]) -> tuple:
    """定位输入框和状态栏。

    Kimi Code TUI 布局：
    ```
    ╭───╮ 输入框顶框
    │ > 输入框文本
    ╰───╯ 输入框底框
    sensenova-6.8-flash-lite thinking  cwd  tip  context: N%  ← 状态栏（1-2行）
    ```

    Returns:
        (input_text, bar_text)
    """
    input_text = ""
    bar_text = ""

    # 找到所有分隔线索引（框底 ╰── 或纯 ── 线）
    sep_idxs = [i for i, l in enumerate(lines) if _SEPARATOR_RE.search(l)]

    if not sep_idxs:
        return input_text, bar_text

    # 最后一条分隔线 → 其下为状态栏
    last_sep = sep_idxs[-1]
    bar_parts: List[str] = []
    for j in range(last_sep + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped:
            bar_parts.append(stripped)
    bar_text = " ".join(bar_parts)

    # 输入框：最后一条分隔线上方最近的 `>` 行
    # 注意：输入框可能被 ╭─╮ 框包裹，> 在框内
    upper_sep = sep_idxs[-2] if len(sep_idxs) >= 2 else -1
    search_start = upper_sep + 1 if upper_sep >= 0 else 0
    for i in range(last_sep - 1, search_start - 1, -1):
        m = _INPUT_RE.match(lines[i])
        if m:
            text = m.group(1).strip()
            if text and not _SEPARATOR_RE.search(text):
                if _PLACEHOLDER_MARK in text:
                    text = ""
                input_text = text
            break

    return input_text, bar_text


def _clean_frame(value: str) -> str:
    """清理欢迎页框内提取的值：去掉尾部框线字符与空白。"""
    return value.strip().rstrip("│╮╯").strip()


def _extract_model_display(lines: List[str]) -> str:
    """从欢迎页框或状态栏提取模型显示名。

    优先状态栏（`sensenova-6.8-flash-lite thinking`），
    欢迎页 `Model: <name>` 行兜底。
    `not set` 表示未配置模型，返回空。
    """
    # 状态栏：右起查找包含 model 的行（bar 会在最后几行）
    # 状态栏格式：`sensenova-6.8-flash-lite thinking  cwd  tip  context: N%`
    for line in lines:
        s = line.strip()
        # 状态栏特征：model 名 + 空格 + thinking 或 context: 在同一行
        if "thinking" in s and " " in s:
            # 提取 model 名（"thinking" 前的文本）
            idx = s.find("thinking")
            if idx > 0:
                # 取 thinking 前的部分，去除末尾空格
                before = s[:idx].strip()
                if before and not " " in before.strip():
                    # 纯 model 名称（无空格）
                    return before
                # 可能包含路径，取第一个空格前的部分
                parts = before.split()
                if parts:
                    return parts[0]
    # 欢迎页兜底
    for line in lines:
        s = line.strip()
        if "Model:" in s:
            parts = s.split("Model:", 1)
            if len(parts) > 1:
                value = _clean_frame(parts[1])
                if value and "not set" not in value:
                    return value
    return ""


def _extract_cwd_display(lines: List[str]) -> str:
    """从屏幕提取工作目录显示。

    欢迎页 `Directory: <path>` 行，或状态栏 cwd 部分。
    """
    # 欢迎页
    for line in lines:
        s = line.strip()
        if "Directory:" in s:
            parts = s.split("Directory:", 1)
            if len(parts) > 1:
                return _clean_frame(parts[1])
    # 状态栏：sensenova-6.8-flash-lite thinking  C:\path  tip  context: N%
    for line in lines:
        s = line.strip()
        if "thinking" in s:
            # 在 "thinking" 之后找路径
            idx = s.find("thinking")
            after = s[idx + len("thinking"):].strip()
            # 路径通常是第一个带 \ 或 / 的段
            for part in after.split():
                if re.match(r"^[A-Za-z]:[\\/]", part) or part.startswith("/"):
                    return part
    return ""


def _extract_version_display(lines: List[str]) -> str:
    """从欢迎页框提取版本号。"""
    for line in lines:
        s = line.strip()
        if "Version:" in s:
            parts = s.split("Version:", 1)
            if len(parts) > 1:
                return _clean_frame(parts[1])
    return ""


def _extract_context_percent(bar_text: str, lines: Optional[List[str]] = None) -> Optional[int]:
    """从状态栏文本提取上下文百分比。

    Kimi 格式：`context: 10% (23.3k/256k)`
    宽屏下 `%` 可能被拆到下一行，先尝试整行再尝试跨行。
    """
    if lines:
        for line in lines:
            m = _CONTEXT_RE.search(line)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    return None
    if not bar_text:
        return None
    # 跨行拼接
    joined = re.sub(r"\s+", "", bar_text)
    m = re.search(r"context:(\d+)%", joined)
    if m:
        try:
            return int(m.group(1))
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

    # 界面类型
    joined = "\n".join(lines)
    has_welcome = _WELCOME_HEADER in joined and _WELCOME_FRAME_TOP in joined
    has_conversation = any(kw in joined for kw in _CONVERSATION_MARKS)
    state.screen_type = (
        "conversation" if (has_conversation or not has_welcome) else "main"
    )

    input_text, bar_text = _find_input_and_status(lines)
    state.input_text = input_text

    # 状态栏字段
    state.context_percent = _extract_context_percent(bar_text, lines)

    # 工作目录、模型、版本
    state.cwd_display = _extract_cwd_display(lines)
    state.model_display = _extract_model_display(lines)
    state.version_display = _extract_version_display(lines)

    # AI 状态
    state.ai_status = _detect_ai_status(lines)

    _log.debug("parse_screen_lines: status=%s input=%r screen=%s model=%r ctx=%s",
               state.ai_status, state.input_text, state.screen_type, state.model_display,
               state.context_percent)
    return state


def parse_screen_snapshot(vt_text: str, columns: int = 0, rows: int = 0) -> LiveState:
    """从带 VT 序列的屏幕全量文本提取 LiveState。

    Args:
        vt_text: 纯 VT 输出（不含 PTY-Agent 元数据 header/footer）
        columns: 终端列数；0 则自动检测
        rows: 终端行数；0 则自动检测

    Returns:
        LiveState 实体
    """
    lines = parse_screen(vt_text, columns=columns, rows=rows, rstrip=False)
    return parse_screen_lines(lines)