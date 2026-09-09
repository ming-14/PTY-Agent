r"""屏幕快照解析适配器：从 omp TUI 屏幕提取实时状态（LiveState）。

omp（oh-my-pi）TUI 布局（v17.4.2，实测，unicode 符号预设）：

欢迎页（main）：
```
╭─── omp v17.4.2 ───────────────────────────────────────────────────╮
│                          │ Tips                                   │
│      Welcome back!       │ # for prompt actions                   │
│       ▀██████████▀       │ / for commands                         │
│        ╘██    ██         │ ...                                    │
│ Sensenova 6.8 flash lite │                                        │
│         litellm          │                                        │
╰──────────────────────────┴────────────────────────────────────────╯
 Tip: ...
────────────────────────────────────────────────────────────────────
 Update Available
 New version 18.0.1 is available. Run: omp update
────────────────────────────────────────────────────────────────────

╭── π  > ⬢ Sensenova 6.8 flash lite · ◒ high > 📁 ~/Desktop/ompparser > ◫ 5.8%/262K ⟲ ▶──...─╮
╰─                                                                                            ─╯
```

对话中（conversation）：
```
 （thinking，前导空格斜体）
 User wants to check if Python is installed.

 ⓘ Bash
  └─ command="python --version"
 ⟦Ctrl+O: Expand⟧

 （用户消息）
 run a slow command: ping -n 20 127.0.0.1

 （工具执行框）
╭──────────────────────────────────────╮
│ $ ping -n 20 127.0.0.1               │
├─── Output ───────────────────────────┤
│ ping: ok                             │
│ ⟦Wall: 20.02s | Timeout: disabled⟧   │
╰──────────────────────────────────────╯
 Done.
 完成,ping 了 127.0.0.1 ...

╭── π  > ⬢ Sensenova 6.8 flash lite · ◒ high > 📁 ~/Desktop/ompparser > ◫ 7.7%/262K ⟲ ▶──...─╮
╰─                                                                                            ─╯
```

工作中：`⠋ Working… ⟦esc⟧`（spinner 帧 ⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏）

关键布局特征：
- **状态栏 = 编辑器顶边框**（`╭── π  > ⬢ Model · ◒ level > 📁 path > ◫ N%/NNNK ⟲ ▶...`）
- **输入框 = 编辑器底边框**（`╰─ <input text> ─╯`，输入文字直接显示在底边框内）
- 欢迎页大框 `╭─── omp v17.4.2 ───╮` 判定 main
- powerline-thin 分隔符 `>`，segment 由 ` > ` 分隔
- 图标：`π`=pi、`⬢`=model、`📁`=path、`◫`=context、`⟲`=auto、`▶`=端帽
- 思考等级：`◒ high`（◒◕◑◔◉○ ⟳ 等）

解析策略：从底部往上定位编辑器边框（顶框=状态栏，底框=输入框），
从状态栏提取 model/thinking/path/context，从底框提取输入文字。
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..entities import LiveState
from ..infra.logging import get_logger
from ..infra.vt import parse_screen

_log = get_logger("screen")

# ── 正则 ──

# 欢迎页大框标题：╭─── omp v17.4.2 ───
_WELCOME_RE = re.compile(r"^\s*╭───\s*omp\s+v")

# 欢迎页关键词（main 判定，欢迎框可能滚出屏幕时兜底）
_WELCOME_KEYWORDS = (
    "Welcome back!",
    "for prompt actions",
    "Tip:",  # 欢迎页提示行（仅 main 时出现）
    "Update Available",  # 启动更新横幅（仅 main 时出现，窄屏顶线滚出后仍可见）
)

# 工作中 spinner：⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ Working… ⟦esc⟧
_WORKING_RE = re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s*(?:Working|Thinking|Compacting|Retrying|Streaming)")
_WORKING_KEYWORD = "Working…"

# 工具执行框：$ cmd（框内命令行）
_TOOL_CMD_RE = re.compile(r"^\s*[$>]\s(.+)$")

# 工具完成标记：⟦Wall: 20.02s | Timeout: disabled | Artifact: 1⟧
_TOOL_DONE_RE = re.compile(r"⟦(?:Wall|Took|Duration)")

# 工具状态行：ⓘ Bash / ● Read ...（工具标题）
_TOOL_STATUS_RE = re.compile(r"^\s*(?:ⓘ|●)\s*([A-Za-z_][A-Za-z0-9_]*)\b")

# 编辑器顶边框（状态栏）：╭── π  > ⬢ Model · ◒ level > 📁 path > ◫ ctx ...
_EDITOR_TOP_RE = re.compile(r"^\s*╭──")

# 编辑器底边框（输入框）：╰─ ... ─╯
_EDITOR_BOTTOM_RE = re.compile(r"^\s*╰─")

# 状态栏字段
_MODEL_RE = re.compile(r"⬢\s*([^>·│]+)")                              # ⬢ Model
_THINKING_RE = re.compile(r"·\s*(◒|◕|◑|◔|◉|○|⟳|thinking\s+off|off)")  # · ◒ high
_PATH_RE = re.compile(r"📁\s*([^>│]+)")                                # 📁 ~/path
_CONTEXT_RE = re.compile(r"◫\s*(\d+(?:\.\d+)?)%/(\d+(?:\.\d+)?[kK]?|\?)")  # ◫ 5.8%/262K
_CONTEXT_QMARK_RE = re.compile(r"◫\s*(\d+(?:\.\d+)?[kK]?|\?)/\?")     # ◫ N/? (unknown window)

# 思考等级 → 标准名映射
_THINKING_MAP = {
    "◒": "high", "◕": "xhigh", "◑": "medium", "◔": "low",
    "◉": "max", "○": "minimal", "⟳": "auto",
    "thinking off": "off", "off": "off",
}

# 光标/反色块标记（输入行可能含光标）
_CURSOR_RE = re.compile(r"[▌█]")


def parse_screen_snapshot(snapshot_text: str) -> LiveState:
    """解析屏幕快照文本（可含 ANSI/VT 序列）为 LiveState。

    Args:
        snapshot_text: PTY-Agent --keep-ansi 输出或纯文本快照

    Returns:
        LiveState
    """
    lines = parse_screen(snapshot_text) if "\x1b" in snapshot_text else _split_lines(snapshot_text)
    _log.debug("screen snapshot: %d lines", len(lines))
    return _parse_lines(lines)


def _split_lines(text: str) -> List[str]:
    """纯文本快照按行拆分。"""
    return [l.rstrip() for l in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def _parse_lines(lines: List[str]) -> LiveState:
    state = LiveState()
    if not lines:
        return state

    # ── 定位编辑器边框：顶框（状态栏）+ 底框（输入框）──
    top_idx: Optional[int] = None
    bottom_idx: Optional[int] = None
    for i, l in enumerate(lines):
        if _EDITOR_TOP_RE.match(l):
            top_idx = i  # 取最后一个（屏幕底部）
        if _EDITOR_BOTTOM_RE.match(l):
            bottom_idx = i  # 取最后一个

    # ── 屏幕类型 ──
    state.screen_type = "main" if _is_welcome(lines) else "conversation"

    # ── 状态栏字段（顶边框）──
    if top_idx is not None:
        _parse_status_line(lines[top_idx], state)

    # ── 输入框文字（底边框）──
    if bottom_idx is not None:
        state.input_text = _extract_input_text(lines[bottom_idx])

    # ── AI 状态 ──
    state.ai_status = _detect_ai_status(lines)

    _log.debug("live_state: status=%s screen=%s ctx=%.1f%% model=%s thinking=%s",
               state.ai_status, state.screen_type, state.context_percent,
               state.model_display, state.thinking_level)
    return state


def _parse_status_line(line: str, state: LiveState) -> None:
    """从状态栏（编辑器顶边框）提取字段。"""
    # 模型：⬢ Model（到分隔符 > 或边框 │ 为止）
    m = _MODEL_RE.search(line)
    if m:
        model = m.group(1).strip()
        # 去除尾部分隔符/思考等级残留
        model = re.sub(r"\s*[·>│].*$", "", model).strip()
        state.model_display = model

    # 思考等级：· ◒ high
    m = _THINKING_RE.search(line)
    if m:
        glyph = m.group(1).strip()
        # 等级文本在 glyph 后到下一个分隔符之前
        rest = line[m.end():]
        level_match = re.match(r"\s*([a-zA-Z]+)", rest)
        if level_match and level_match.group(1) in (
                "off", "minimal", "low", "medium", "high", "xhigh", "max", "auto"):
            state.thinking_level = level_match.group(1)
        else:
            state.thinking_level = _THINKING_MAP.get(glyph, "")

    # 路径：📁 ~/path（到分隔符 > 或边框 │ 或 end cap ▶ 或行尾为止）
    m = _PATH_RE.search(line)
    if m:
        path = m.group(1).strip()
        # 去除尾部 > 分隔符、│ 边框、▶ end cap、── 填充、╮ 边框
        path = re.sub(r"\s*[>│▶╮].*$", "", path).strip()
        state.cwd_display = path

    # 上下文：◫ N%/NNNK
    m = _CONTEXT_RE.search(line)
    if m:
        try:
            state.context_percent = float(m.group(1))
        except ValueError:
            pass
        win = m.group(2)
        try:
            if win.lower().endswith("k"):
                state.context_window = int(float(win[:-1]) * 1000)
            else:
                state.context_window = int(win)
        except ValueError:
            pass
    else:
        m = _CONTEXT_QMARK_RE.search(line)
        if m:
            win = m.group(1)
            try:
                if win.lower().endswith("k"):
                    state.context_window = int(float(win[:-1]) * 1000)
                else:
                    state.context_window = int(win)
            except ValueError:
                pass


def _extract_input_text(line: str) -> str:
    """从编辑器底边框提取输入文字。

    omp 的输入框是编辑器底边框：`╰─ <input text> ─╯`。
    边框格式：╰─ + 内容 + ─╯（右侧 ─╯ 可能紧跟内容无空格）。
    """
    s = line.strip()
    if not s.startswith("╰─"):
        return ""
    # 去掉左侧 ╰─ 前缀
    rest = s[2:].strip()
    # 去掉右侧 ─╯ 后缀（内容后可能紧跟 ─）
    rest = re.sub(r"─*\s*╯\s*$", "", rest)
    # 去掉光标标记
    text = _CURSOR_RE.sub("", rest).strip()
    return text


def _is_welcome(lines: List[str]) -> bool:
    """判断是否欢迎页（main）。

    规则：欢迎页大框（╭─── omp v）存在于屏幕中，或
    欢迎页内容特征（Welcome back!/Recent sessions/LSP Servers）存在且
    编辑器上方无对话内容（用户消息/工具块）。
    """
    # 定位欢迎页大框起始行与编辑器顶框行
    welcome_idx: Optional[int] = None
    editor_idx: Optional[int] = None
    for i, l in enumerate(lines):
        if _WELCOME_RE.match(l):
            welcome_idx = i
        if _EDITOR_TOP_RE.match(l):
            editor_idx = i

    # 欢迎页大框不可见（窄屏顶线滚出屏幕）：靠内容特征判定
    if welcome_idx is None:
        joined = "\n".join(lines)
        has_welcome_content = any(kw in joined for kw in _WELCOME_KEYWORDS)
        if not has_welcome_content:
            return False
        # 检查编辑器上方是否有对话特征
        if editor_idx is not None:
            upper = "\n".join(lines[:editor_idx])
            if _has_conversation_markers(upper):
                return False
        return True

    # 如果编辑器顶框不存在，靠关键词判定
    if editor_idx is None:
        joined = "\n".join(lines)
        return any(kw in joined for kw in _WELCOME_KEYWORDS)

    # 欢迎页大框必须在编辑器顶框上方
    if welcome_idx >= editor_idx:
        return False

    # 找欢迎页大框底部边框（╰─...─╯，可能含 ┴ 分隔）
    box_bottom = welcome_idx + 1
    while box_bottom < editor_idx:
        s = lines[box_bottom].strip()
        if s.startswith("╰") and s.endswith("╯"):
            break
        box_bottom += 1

    # 大框与编辑器之间的区域（对话内容区）
    middle = "\n".join(lines[box_bottom + 1:editor_idx])

    # 对话特征：工具框 / 工具状态行 / 工作中 spinner
    if _has_conversation_markers(middle):
        return False

    # 无对话特征 → main
    return True


def _has_conversation_markers(text: str) -> bool:
    """检测文本中是否有对话内容特征（工具框/工具状态行/工作中）。"""
    if re.search(r"╭─[^╰]*\$", text) and "Output" not in text:
        return True
    if _TOOL_STATUS_RE.search(text):
        return True
    if _WORKING_RE.search(text):
        return True
    return False


def _detect_ai_status(lines: List[str]) -> str:
    """从屏幕内容检测 AI 状态。

    优先级：权限请求 > 工具执行中 > 工作中 > 空闲
    """
    joined = "\n".join(lines)

    # 权限请求（plan 批准框 / ask dialog）
    if _is_approval_dialog(lines):
        return "awaiting_approval"

    # ask dialog（AskUserQuestion 工具提问）
    if _is_ask_dialog(lines):
        return "asking"

    # 工作中 spinner
    if _WORKING_RE.search(joined) or _WORKING_KEYWORD in joined:
        if _has_running_tool(lines):
            return "tool_running"
        return "working"

    # 工具执行框且无完成标记 → tool_running
    if _has_running_tool(lines):
        return "tool_running"

    return "idle"


def _is_approval_dialog(lines: List[str]) -> bool:
    """检测权限请求框（plan 批准：Approve and execute 选项列表）。"""
    joined = "\n".join(lines)
    return bool(
        ("Approve and execute" in joined or "Do you want to proceed?" in joined)
        and ("Refine plan" in joined or "1." in joined or "Yes" in joined)
    )


def _is_ask_dialog(lines: List[str]) -> bool:
    """检测 ask_user_question 提问框及其子状态（选项列表 / note 输入）。

    omp 的 ask 对话框特征：
    - 主对话框：`╭─ Ask ───╮` + `Enter select · n note · ↑/↓ move · Esc cancel`
    - 摘要框：`╭─── Ask 1 questions ───╮` + `[lang] · options:N`
    - note 输入：`╭─ Note for <option> ───╮` + `enter or ctrl+q submit`
    - 选项标记：`(Recommended)` / `○` / `❯` / `○ Other (type your own)`
    """
    joined = "\n".join(lines)

    # 交互式选项列表（主对话框）
    if ("Enter select" in joined or "Enter to select" in joined
            or "Space/Enter toggle" in joined or "n note" in joined):
        return True

    # note 输入框（ask 子状态）
    if "ctrl+q submit" in joined or "Note for " in joined:
        return True

    # 摘要框（Ask N questions + options:N tab）
    if ("Ask" in joined and "questions" in joined
            and ("options:" in joined or "Enter select" in joined)):
        return True

    # 纯选项标记（推荐项 + 自定义项，对话框内容特征）
    if "(Recommended)" in joined and ("type your own" in joined or "Enter select" in joined):
        return True

    return False


def _has_running_tool(lines: List[str]) -> bool:
    """检测是否存在进行中的工具执行。

    特征：工具状态行（ⓘ/● ToolName）或 $ cmd 行存在，
    且其下方无对应的完成标记（⟦Wall/Took/Duration⟧）。
    """
    # 找最后一个工具框起始
    box_start = None
    for i, l in enumerate(lines):
        if l.strip().startswith("╭─") and _TOOL_CMD_RE.search(l):
            box_start = i
        elif _TOOL_CMD_RE.search(l) and "└─" not in l:
            box_start = i

    if box_start is None:
        return False
    # 检查工具框之后是否有完成标记
    for l in lines[box_start:]:
        if _TOOL_DONE_RE.search(l):
            return False
    return True
