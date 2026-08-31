"""屏幕快照解析适配器：从 Claude Code TUI 屏幕提取实时状态（LiveState）。

Claude Code TUI 布局（v2.1.240，实测）：
```
[提示横幅（未知模型警告等，可有可无）]
╭─── Claude Code v2.1.240 ─────╮   ← 欢迎页大框（首会话显示）
│   sensenova-6.8-flash-lite · API Usage Billing │
│               ~\Desktop\example-project              │
╰──────────────────────────────╯
[消息区（含 scrollback）]
──────────────────────────────────────────────────  ← 分隔线（─ 连续）
> <输入框>                                        ← 输入框（提示符 >）
──────────────────────────────────────────────────
  ⏸ manual mode on · ? for shortcuts · ← for agents   ← 状态栏（左）
                          ● high · /effort            ← 状态栏（右，effort）
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

# 分隔线：整行几乎全为 ─（Claude Code 消息区/输入框分隔）
# 注意不能匹配框线（╭───...）或内容中的局部横线
_SEPARATOR_RE = re.compile(r"^\s*─{10,}\s*$")

# 输入框：以 > 开头（提示符后跟文本）
_INPUT_RE = re.compile(r"^>\s?(.*)$")

# 状态栏：权限模式（manual mode on / auto-accept edits 等）
# 已知模式关键词（Claude Code 状态栏左侧）
_PERMISSION_MODES = (
    "manual mode on",
    "auto-accept edits",
    "auto-accept build commands",
    "bypass permissions",
    "auto-accept",
    "plan mode on",
)
_PERMISSION_RE = re.compile(r"⏸\s*([^·|]{2,40})")

# 状态栏右侧：effort（● high · /effort）
_EFFORT_RE = re.compile(r"●\s*(high|medium|low|auto)\s*·\s*/effort")

# 思考中：● Thinking for Xs ... (ctrl+o to expand)
_THINKING_RE = re.compile(r"●\s*Thinking for")

# 工具执行中：✢/✻ 前缀 + 现在进行时，或 * 前缀（Claude Code 2.1.240 工具行）
_TOOL_RUNNING_RE = re.compile(r"(?:[✢✻]\s*(?:Hullaballooing|searching|reading|writing|editing|running)|\*\s*(?:Booping|Bloop|Working|searching|thinking|reading|writing))")

# 权限请求关键词（工具权限批准框）
_PERMISSION_REQUEST_KEYWORDS = ("Do you want to proceed?", "Tab to amend")

# AskUserQuestion 情景关键词（Claude 向用户提问）
_ASK_KEYWORDS = ("Enter to select · ↑/↓ to navigate · Esc to cancel", "Chat about this", "Type something")
_ASK_HEADER_RE = re.compile(r"\[ \]\s*\S")  # 选项框标题 "[ ] header"

# 思考中关键词（进行中状态）
# 注意："(ctrl+o to expand)" 是已完成思考的折叠提示，不表示进行中
_THINKING_KEYWORDS = ("Thinking for", "(esc to cancel)")

# 欢迎页特征（顶框或底框；窄屏时顶框可能滚出屏幕）
_WELCOME_MARK = "╭─── Claude Code"
_WELCOME_BOTTOM = "╰────"  # 欢迎页底框（窄屏仍可见）

# 旋转动画字符集
_SPINNER_CHARS = frozenset("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")


def _detect_ai_status(lines: List[str]) -> str:
    """从消息区检测 AI 状态。

    Returns:
        idle / thinking / tool_running / awaiting_approval / awaiting_answer
    """
    scan_text = "\n".join(lines)

    # AskUserQuestion 情景（先于权限批准判定，避免 "Esc to cancel" 误判）
    if any(kw in scan_text for kw in _ASK_KEYWORDS) or _ASK_HEADER_RE.search(scan_text):
        return "awaiting_answer"

    for kw in _PERMISSION_REQUEST_KEYWORDS:
        if kw in scan_text:
            return "awaiting_approval"

    if _THINKING_RE.search(scan_text):
        return "thinking"

    for kw in _THINKING_KEYWORDS:
        if kw in scan_text:
            return "thinking"

    if _TOOL_RUNNING_RE.search(scan_text):
        return "tool_running"

    # 状态栏 "esc to interrupt" 表示 AI 正在工作
    if "esc to interrupt" in scan_text:
        return "tool_running"

    for line in lines:
        if any(ch in _SPINNER_CHARS for ch in line):
            return "tool_running"

    return "idle"


def _find_input_and_status(lines: List[str]) -> tuple:
    """定位输入框和状态栏。

    Claude Code TUI 布局（分隔线分割）：
    ```
    ──────────── 分隔线1（可选）
    > 输入框
    ──────────── 分隔线2
    ⏸ manual mode on ...    ● high · /effort   ← 状态栏（分隔线2 之下）
    ```

    策略：
    1. 从底部往上找第一条分隔线 sep1 → 其下所有非空行 = 状态栏
    2. 在 sep1 之上找第二条分隔线 sep2 → sep2 与 sep1 之间的 `>` 行 = 输入框
    3. 若没有 sep2，sep1 之上最近的 `>` 行 = 输入框

    Returns:
        (input_text, bar_lines)
        input_text: 输入框文字
        bar_lines: 状态栏非空行列表
    """
    input_text = ""
    bar_lines: List[str] = []

    # 1. 找到所有分隔线索引（从下到上）
    sep_idxs = [i for i, l in enumerate(lines) if _SEPARATOR_RE.search(l)]

    if not sep_idxs:
        return input_text, bar_lines

    # 最后一条分隔线 → 其下为状态栏
    last_sep = sep_idxs[-1]
    for j in range(last_sep + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped:
            bar_lines.append(stripped)

    # 2. 状态栏上方的分隔线（倒数第二条，可选）
    upper_sep = sep_idxs[-2] if len(sep_idxs) >= 2 else -1

    # 3. 输入框：upper_sep 之后、last_sep 之前的最后一个 `>` 行
    search_start = upper_sep + 1 if upper_sep >= 0 else 0
    for i in range(last_sep - 1, search_start - 1, -1):
        m = _INPUT_RE.match(lines[i])
        if m:
            text = m.group(1).strip()
            # 过滤纯分隔符残留（窄屏/渲染重叠时输入框行可能混入 ─）
            if text and not _SEPARATOR_RE.search(text):
                input_text = text
            break

    return input_text, bar_lines


def _extract_cwd_display(lines: List[str]) -> str:
    """从欢迎页大框提取工作目录显示（如 ~\\Desktop\\example-project）。

    只匹配大框内的目录行：`│   ~\path  │`（含 ~ 或盘符前缀）。
    """
    for line in lines:
        s = line.strip()
        if not s.startswith("│") or not s.endswith("│"):
            continue
        inner = s.strip("│ ").strip()
        if not inner:
            continue
        # 目录特征：~ 开头 或 盘符（C:\）开头，且不是 "Claude Code" 标题
        if inner.startswith("Claude Code"):
            continue
        if inner.startswith("~") or re.match(r"^[A-Za-z]:[\\/]", inner):
            return inner
    return ""


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

    # 界面类型：欢迎页 vs 对话中
    # 欢迎页特征：顶框 ╭─── Claude Code（宽屏）或 底框 ╰────（窄屏顶框滚动出屏幕）
    # 注意：对话中也可能残留欢迎页框（重绘残留），有对话内容标记时优先判对话中
    joined = "\n".join(lines)
    has_welcome_frame = _WELCOME_MARK in joined or _WELCOME_BOTTOM in joined
    has_conversation_mark = (
        any(kw in joined for kw in _PERMISSION_REQUEST_KEYWORDS)
        or any(kw in joined for kw in _ASK_KEYWORDS)
        or _ASK_HEADER_RE.search(joined) is not None
        or _THINKING_RE.search(joined) is not None
        or "Thought for" in joined
        or _TOOL_RUNNING_RE.search(joined) is not None
    )
    state.screen_type = (
        "conversation" if (has_conversation_mark or not has_welcome_frame) else "main"
    )

    input_text, bar_lines = _find_input_and_status(lines)
    state.input_text = input_text

    # 状态栏字段
    for bar in bar_lines:
        m = _EFFORT_RE.search(bar)
        if m:
            state.effort = m.group(1)
        if not state.permission_mode:
            # 优先用已知模式关键词匹配
            for kw in _PERMISSION_MODES:
                if kw in bar:
                    state.permission_mode = kw
                    break
            if not state.permission_mode:
                m = _PERMISSION_RE.search(bar)
                if m:
                    candidate = m.group(1).strip()
                    if candidate:
                        state.permission_mode = candidate

    # 工作目录显示
    state.cwd_display = _extract_cwd_display(lines)

    # AI 状态
    state.ai_status = _detect_ai_status(lines)

    _log.debug("parse_screen_lines: status=%s effort=%s perm=%s input=%r screen=%s",
               state.ai_status, state.effort, state.permission_mode, state.input_text, state.screen_type)
    return state


def parse_screen_snapshot(vt_text: str) -> LiveState:
    """从带 VT 序列的屏幕全量文本提取 LiveState。

    Args:
        vt_text: 纯 VT 输出（不含 pty-agent 元数据 header/footer）
                 推荐用 pty-agent -o 输出到文件获取干净内容

    Returns:
        LiveState 实体
    """
    lines = parse_screen(vt_text, rstrip=False)
    return parse_screen_lines(lines)