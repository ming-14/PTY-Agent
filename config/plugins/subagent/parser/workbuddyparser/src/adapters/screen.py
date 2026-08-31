"""屏幕快照解析适配器：从 CodeBuddy Code TUI 屏幕提取实时状态（LiveState）。

CodeBuddy Code TUI 布局（v2.137.1，实测）：
```
[横幅/警告（可有可无）]
╭─── CodeBuddy Code v2.137.1 ────────────╮   ← 欢迎页大框（首会话显示）
│  ... Tips ...                          │
│  Recent activity                       │
│  http://127.0.0.1:<port>               │
│  Hy3 · internal Usage Billing          │
│  c:\\Users\\<user>\\Desktop\\<project>      │
╰────────────────────────────────────────╯

√ Hook SessionStart
  ⚠️ agent-browser 目前不支持 Windows 系统

──────────────────────────────────────────  ← 分隔线（─ 连续）
> <输入框>                                  ← 输入框（提示符 >）
──────────────────────────────────────────
? for shortcuts  ← 1 agent                 ← 状态栏
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
# 注意不能匹配框线（╭───...）或内容中的局部横线
_SEPARATOR_RE = re.compile(r"^\s*─{10,}\s*$")

# 输入框：以 > 开头（提示符后跟文本）
_INPUT_RE = re.compile(r"^>\s?(.*)$")

# 状态栏：? for shortcuts  ← 1 agent
_STATUS_BAR_RE = re.compile(r"(\? for shortcuts)")

# 思考开关（状态栏右侧）
_THINKING_ON_RE = re.compile(r"Thinking\s+(on|off)\s*\(AltT to toggle\)")

# 工作中状态：✶ Waking… / ✹ Zooming… 等 (Ns · preparing/streaming · ↑/↓ N tokens · esc to interrupt)
_WAKING_RE = re.compile(r"[✶✷✸✹✺✻✼✽✾✿❀❁❂❃❄❅❆❇❈❉❊❋*]+\s*(?:Waking|Zooming|Observing|Syncing|Thinking)")

# 工具执行中：● Bash(...) / ● Read(...) 等
_TOOL_RUNNING_RE = re.compile(r"^●\s*[A-Za-z]+\(.*\)")

# 权限请求关键词（工具权限批准框 + 首次运行信任对话框）
_PERMISSION_REQUEST_KEYWORDS = (
    "Do you want to proceed?",
    "Press enter to confirm or esc to cancel",
    "Do you trust the files in this folder?",
)

# AskUserQuestion 提问对话框关键词（AI 向用户提问，等待选择）
_ASKING_KEYWORDS = (
    "Enter to select · ↑/↓ to navigate · Esc to cancel",
    "Enter to select",
    "↑/↓ to navigate",
    "Type something",
)

# 欢迎页特征（顶框）
_WELCOME_MARK = "╭─── CodeBuddy Code"
_WELCOME_BOTTOM = "╰────"  # 欢迎页底框（窄屏仍可见）

# 对话内容标记（用于区分 main/conversation）
# 注意：↵ send（输入框 placeholder）在欢迎页也存在，不能作为对话标记
_CONVERSATION_MARKS = (
    "Do you want to proceed?",
    "esc to interrupt",
    "Waking",
    "Thinking on",
    "● ",            # assistant 回复前缀（欢迎页无此字符）
    "> hi",          # 用户消息回显
)

# 输入框 placeholder（不可作为实际输入）
_PLACEHOLDER_MARK = "↵ send"


def _detect_ai_status(lines: List[str]) -> str:
    """从消息区检测 AI 状态。

    Returns:
        idle / thinking / tool_running / awaiting_approval / asking
    """
    scan_text = "\n".join(lines)

    # 权限请求（awaiting_approval）优先于 asking
    for kw in _PERMISSION_REQUEST_KEYWORDS:
        if kw in scan_text:
            return "awaiting_approval"

    # AI 提问对话框（AskUserQuestion）
    for kw in _ASKING_KEYWORDS:
        if kw in scan_text:
            return "asking"

    if _WAKING_RE.search(scan_text):
        return "thinking"

    # 思考行：∴ Thinking...
    if "∴ Thinking" in scan_text or "  Thinking" in scan_text:
        return "thinking"

    # 工具行 + 输出：● Bash(cmd) 后跟 ⎿ 输出 → 工具执行中
    if _TOOL_RUNNING_RE.search(scan_text):
        return "tool_running"

    # 状态栏 "esc to interrupt" 表示 AI 正在工作
    if "esc to interrupt" in scan_text:
        return "tool_running"

    # 以上都不匹配 → 检查是否是已知的 TUI 结构（有输入框或状态栏）
    # 都不是 → 未知界面（无法解析）
    if any(_INPUT_RE.match(l) for l in lines) or any(_STATUS_BAR_RE.search(l) for l in lines):
        return "idle"
    return "unknown"


def _find_input_and_status(lines: List[str]) -> tuple:
    """定位输入框和状态栏。

    CodeBuddy Code TUI 布局（分隔线分割）：
    ```
    ──────────── 分隔线1（可选）
    > 输入框
    ──────────── 分隔线2
    ? for shortcuts  ← 1 agent   ← 状态栏（分隔线2 之下）
    ```

    策略：
    1. 从底部往上找第一条分隔线 sep1 → 其下所有非空行 = 状态栏
    2. 在 sep1 之上找第二条分隔线 sep2 → sep2 与 sep1 之间的 `>` 行 = 输入框
    3. 若没有 sep2，sep1 之上最近的 `>` 行 = 输入框

    Returns:
        (input_text, bar_lines)
        input_text: 输入框文字（不含 placeholder）
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
            # 过滤纯分隔符残留
            if text and not _SEPARATOR_RE.search(text):
                # placeholder（↵ send 结尾）不算实际输入
                if _PLACEHOLDER_MARK in text:
                    text = ""
                input_text = text
            break

    return input_text, bar_lines


def _extract_model_display(lines: List[str]) -> str:
    """从欢迎页大框提取模型显示名。

    CodeBuddy 欢迎页右侧：`Hy3 · internal Usage Billing`。
    """
    for line in lines:
        s = line.strip()
        if not s.startswith("│") or not s.endswith("│"):
            continue
        inner = s.strip("│ ").strip()
        if not inner:
            continue
        if "·" in inner and ("Billing" in inner or "Usage" in inner):
            return inner.split("·")[0].strip()
    return ""


def _extract_cwd_display(lines: List[str]) -> str:
    """从欢迎页大框提取工作目录显示（如 c:\\Users\\<user>\\Desktop\\<project>）。"""
    for line in lines:
        s = line.strip()
        if not s.startswith("│") or not s.endswith("│"):
            continue
        inner = s.strip("│ ").strip()
        if not inner:
            continue
        # 目录特征：~ 开头 或 盘符（c:\）开头，且不是 "CodeBuddy Code" 标题
        if inner.startswith("CodeBuddy Code"):
            continue
        if inner.startswith("~") or re.match(r"^[A-Za-z]:[\\/]", inner):
            return inner
    return ""


# 状态栏已知权限模式关键词
_PERMISSION_MODES = (
    "bypass permissions on",
    "bypass permissions",
    "manual mode on",
    "auto-accept edits",
    "auto-accept build commands",
    "auto-accept",
    "plan mode on",
    "fullAccess",
)


def _extract_permission_mode(bar_lines: List[str]) -> str:
    """从状态栏提取权限模式。"""
    for bar in bar_lines:
        for kw in _PERMISSION_MODES:
            if kw in bar:
                return kw
    # 通用模式：⏵⏵ / ⏸ 前缀后跟关键词
    m = re.search(r"[⏵⏸✸][⏵]?\s*([A-Za-z].*?)(?:\s+\u2190|\s*$)", bar_lines[-1] if bar_lines else "")
    if m:
        candidate = m.group(1).strip()
        if candidate and len(candidate) < 50:
            return candidate
    return ""


def _extract_thinking_on(bar_lines: List[str]) -> str:
    """从状态栏提取思考开关状态。"""
    for bar in bar_lines:
        m = _THINKING_ON_RE.search(bar)
        if m:
            return f"Thinking {m.group(1)}"
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
    joined = "\n".join(lines)
    has_welcome_frame = _WELCOME_MARK in joined or _WELCOME_BOTTOM in joined
    has_conversation_mark = any(kw in joined for kw in _CONVERSATION_MARKS)
    state.screen_type = (
        "conversation" if (has_conversation_mark or not has_welcome_frame) else "main"
    )

    input_text, bar_lines = _find_input_and_status(lines)
    state.input_text = input_text

    # 状态栏字段
    state.thinking_on = _extract_thinking_on(bar_lines)
    state.permission_mode = _extract_permission_mode(bar_lines)

    # 工作目录与模型显示
    state.cwd_display = _extract_cwd_display(lines)
    state.model_display = _extract_model_display(lines)

    # AI 状态
    state.ai_status = _detect_ai_status(lines)

    _log.debug("parse_screen_lines: status=%s input=%r screen=%s model=%r",
               state.ai_status, state.input_text, state.screen_type, state.model_display)
    return state


def parse_screen_snapshot(vt_text: str, columns: int = 0, rows: int = 0) -> LiveState:
    """从带 VT 序列的屏幕全量文本提取 LiveState。

    Args:
        vt_text: 纯 VT 输出（不含 PTY-Agent 元数据 header/footer）
                 推荐用 PTY-Agent -o 输出到文件获取干净内容
        columns: 终端列数；0 则自动检测（CodeBuddy 增量渲染下可能偏小，
                 已知尺寸时建议显式传入）
        rows: 终端行数；0 则自动检测

    Returns:
        LiveState 实体
    """
    lines = parse_screen(vt_text, columns=columns, rows=rows, rstrip=False)
    return parse_screen_lines(lines)
