r"""屏幕快照解析适配器：从 Crush TUI 屏幕提取实时状态（LiveState）。

Crush TUI 布局（v0.90.0，实测 120x40）：

欢迎页（main）：
```
  ╱╱╱╱╱╱  Charm™                    v0.90.0 ╱╱╱╱╱╱...
  ╱╱╱╱╱╱ ▄▀▀▀▀ █▀▀▀▀▀▀▀▀▄ █   █ ▄▀▀▀▀ █   █ ...
  ~\\Desktop\\crushparser          ← 工作目录
  ◇  via SenseNova               ← 模型/提供商
  LSPs    MCPs    Skills         ← 三列
  > Ready!                       ← 输入框（提示符 >）
  :::                            ← 输入框装饰
  tab focus chat • ...           ← 状态栏（空闲）
```

对话中（conversation）—— 右侧边栏出现：
```
  │ hello                                    ╱╱╱╱╱╱ Charm™ v0.90.0
    Hello! How can I help?                   （logo）
    ◇  via SenseNova in 7s ──────────────    Untitled Session      ← 标题
  │ list files ...                           ~\\Desktop\\crushparser  ← cwd
    ✓ List .                                  ◇  via SenseNova       ← 模型
       The directory tree ...                 6% (16.5K) $0.00       ← 上下文% (token) $费用
    ◇  via SenseNova in 34s ────────────     Modified Files ──────
                                              ...
  > Ready for instructions                    Skills ───────────────
  :::                                         ● agent-browser
  tab focus chat • ...                        ● bbdown
```

权限请求（awaiting_approval）：
```
  │ Permission Required ╱╱╱╱╱...
  │ Tool bash
  │ Path ~\Desktop\crushparser
  │ Desc ...
  │   sleep 30 && echo done
  │   Allow      Allow for Session      Deny
  │ ←/→ choose • enter confirm • esc exit
  ● Bash sleep 30 && echo done (background=true)
  Requesting permission...
  > Processing...
```

解析策略：侧边栏从右列（~36 列后）提取标题/cwd/模型/上下文；输入框从底部
`>` 行提取；状态栏从末行提取；AI 状态从输入框文本 + 权限弹窗关键词判定。
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..entities import LiveState
from ..infra.logging import get_logger
from ..infra.vt import parse_screen

_log = get_logger("screen")

# ── 正则 ──

# 欢迎页 LOGO 标记（▄▀▀▀▀ 块字符行在左侧，非右侧边栏）
_WELCOME_LOGO_RE = re.compile(r"^\s{0,10}╱╱╱╱╱╱\s+▄▀▀▀▀\s+█")

# 版本号：v0.90.0 / Charm™ 后的版本
_VERSION_RE = re.compile(r"v?(\d+\.\d+\.\d+)")

# 输入框：> 后文本（提示符 > 后跟文本，截断到侧边栏边界前）
_INPUT_RE = re.compile(r"^\s*>\s?(.*?)(?:\s{3,}|$)")

# 侧边栏字段（对话中，右列 ~36 列宽）
# 侧边栏锚定关键词：用于定位侧边栏起始列
_SIDEBAR_ANCHORS = (
    "New Session",
    "Untitled Session",
    "Modified Files",
    "LSPs",
    "MCPs",
    "Skills",
)
# 标题：New Session / Untitled Session / 任意标题行（排除 logo 装饰字符）
_SIDEBAR_TITLE_RE = re.compile(
    r"^\s*(New Session|Untitled Session|(?!\u2571|\u2572|Charm|\u2584|\u2588|[╱╲▄▀█])(?:[^ \s].{0,40}))\s*$"
)
# cwd：侧边栏 ~\... 或盘符路径（允许行尾空白）
_SIDEBAR_CWD_RE = re.compile(r"^\s*([~][\\/][^\s]+|[A-Za-z]:[\\/][^\s]+)\s*$")
# 模型：◇  via SenseNova（侧边栏）
_SIDEBAR_MODEL_RE = re.compile(r"^\s*◇\s+via\s+(.+?)\s*$")
# 上下文：6% (16.5K) $0.00（侧边栏，可能有缩进空格）
_SIDEBAR_CONTEXT_RE = re.compile(
    r"^\s*(\d+)\%\s+\(([\d.]+)K?\)\s+\$([\d.]+)\s*$"
)
# 侧边栏区标签（确认右侧栏存在）
_SIDEBAR_LABEL_RE = re.compile(r"^\s*(Modified Files|LSPs|MCPs|Skills|Context)")

# 紧凑模式头部行：Charm™ CRUSH ╱╱╱ <cwd> • <pct>% • <快捷键>（宽度 < 120 时无侧边栏）
_COMPACT_HEADER_RE = re.compile(
    r"Charm™\s+CRUSH\s+╱╱╱\s+(.+?)(?:\s+•\s+(\d+)\%)?(?:\s+•.*)?$"
)

# 模型元数据行：◇  via SenseNova in 7s / in 56m24s ─────（消息区）
_MODEL_META_RE = re.compile(r"◇\s+via\s+(.+?)(?:\s+in\s+\S+)?\s*─")

# 权限请求关键词
_PERMISSION_KEYWORDS = (
    "Permission Required",
    "Allow for Session",
    "←/→ choose",
    "enter confirm",
)

# question 工具提问关键词（awaiting_answer，等待用户选择/输入）
_ASK_KEYWORDS = (
    "enter select",
    "quick select",
    "prev tab",
    "next tab",
    "Type your own",
    "Something else?",
    "Waiting for tool response",
)

# 工作中关键词（> Working... / > Processing...）
_WORKING_INPUT_RE = re.compile(r"^\s*>\s*(Working|Processing)\.{3}\s*$")

# 思考中：旋转动画字符行（含 Braille 或 ASCII 动画）
_THINKING_ANIM_RE = re.compile(r"[⠁-⣿!@#$%^&*()_+\-=\[\]{}|;:,.<>?~]+.*\d+s")

# 工具执行标记
_TOOL_MARKS = (
    "● ",
    "✓ ",
    "✗ ",
    "Job (Start)",
    "Requesting permission",
)

# 对话内容标记（区别于欢迎页）
_CONVERSATION_MARKS = (
    "via SenseNova",
    "New Session",
    "Untitled Session",
    "Modified Files",
    "│ ",
    "Permission Required",
)


def _detect_ai_status(lines: List[str]) -> str:
    """从屏幕检测 AI 状态。

    Returns:
        idle / thinking / tool_running / awaiting_approval / awaiting_answer
    """
    joined = "\n".join(lines)

    # 权限请求弹窗优先
    for kw in _PERMISSION_KEYWORDS:
        if kw in joined:
            return "awaiting_approval"

    # question 工具提问：等待用户选择/输入
    for kw in _ASK_KEYWORDS:
        if kw in joined:
            return "awaiting_answer"

    # 输入框文本判定
    for line in lines:
        if _WORKING_INPUT_RE.match(line):
            # 区分思考 vs 工具执行
            if _THINKING_ANIM_RE.search(joined):
                return "thinking"
            for mark in _TOOL_MARKS:
                if mark in joined:
                    return "tool_running"
            return "thinking"

    # 状态栏判定：忙碌时状态栏带 "esc cancel" 前缀（yolo 模式下工作期间
    # 输入框 placeholder 保持 "Yolo mode!" 不变，只能靠状态栏区分忙碌/空闲；
    # 空闲状态栏为 "tab focus chat • ..."，且工作中两者并存，esc cancel 是
    # 唯一判据）。工具标记区分思考 vs 工具执行。
    for line in lines:
        if line.strip().startswith("esc cancel"):
            for mark in _TOOL_MARKS:
                if mark in joined:
                    return "tool_running"
            return "thinking"

    return "idle"


def _extract_input_text(lines: List[str]) -> str:
    """从输入框行提取用户输入文字。

    Crush 输入框：`> <text>`，`:::` 为装饰行。
    placeholder（Ready! / Ready? / Ready for instructions / Working... / Processing...）不算输入。
    """
    placeholders = {
        "Ready!",
        "Ready?",
        "Ready...",
        "Ready for instructions",
        "Working...",
        "Processing...",
    }
    for line in lines:
        m = _INPUT_RE.match(line)
        if m:
            text = m.group(1).strip()
            if text and text not in placeholders:
                return text
    return ""


def _detect_sidebar_col(lines: List[str]) -> int:
    """检测侧边栏起始列。

    策略：扫描全屏找侧边栏锚定关键词（New Session / Untitled Session /
    Modified Files / LSPs / MCPs / Skills），记录其列位置；
    取出现频率最高的列作为侧边栏边界。返回 0 表示未检测到侧边栏。
    """
    col_counts: dict = {}
    for line in lines:
        for anchor in _SIDEBAR_ANCHORS:
            idx = line.find(anchor)
            if idx >= 0:
                col_counts[idx] = col_counts.get(idx, 0) + 1
    if not col_counts:
        return 0
    # 取频率最高（并列时取最左）
    best_col = 0
    best_cnt = 0
    for col, cnt in sorted(col_counts.items()):
        if cnt > best_cnt:
            best_cnt = cnt
            best_col = col
    return best_col


def _extract_sidebar(lines: List[str]) -> dict:
    """从侧边栏（右列）提取标题 / cwd / 模型 / 上下文。

    两种布局：
    - 宽屏（>= ~120 列）：右侧边栏与消息区同行，先检测侧边栏起始列再切片
    - 紧凑（< ~120 列）：无侧边栏，cwd / 上下文% 在头部行
      `Charm™ CRUSH ╱╱╱ <cwd> • <pct>% • ...`

    Returns:
        dict with title, cwd_display, model_display, context_percent,
        context_tokens, cost_display, version_display
    """
    result = {
        "title": "",
        "cwd_display": "",
        "model_display": "",
        "context_percent": 0.0,
        "context_tokens": 0,
        "cost_display": "",
        "version_display": "",
    }

    # 版本号：logo 行 Charm™ v0.90.0
    for line in lines:
        m = _VERSION_RE.search(line)
        if m and "Charm" in line:
            result["version_display"] = m.group(1)
            break

    sidebar_col = _detect_sidebar_col(lines)
    if sidebar_col <= 0:
        # 紧凑模式：从头部行提取 cwd / 上下文%
        for line in lines:
            m = _COMPACT_HEADER_RE.search(line)
            if m:
                if not result["cwd_display"] and m.group(1):
                    result["cwd_display"] = m.group(1).strip().rstrip("…")
                if m.group(2):
                    try:
                        result["context_percent"] = float(m.group(2))
                    except ValueError:
                        pass
        return result

    # 按侧边栏列切片，提取各字段
    for line in lines:
        if len(line) <= sidebar_col:
            continue
        side = line[sidebar_col:]

        m = _SIDEBAR_CONTEXT_RE.match(side)
        if m:
            try:
                result["context_percent"] = float(m.group(1))
            except ValueError:
                pass
            # token：16.5K → 16500；16.3 → 16（无 K 后缀按原始值）
            tok = m.group(2)
            try:
                if tok.endswith("K") or "K" in tok:
                    result["context_tokens"] = int(float(tok.replace("K", "")) * 1000)
                else:
                    result["context_tokens"] = int(float(tok))
            except ValueError:
                pass
            result["cost_display"] = m.group(3)
            continue

        m = _SIDEBAR_MODEL_RE.match(side)
        if m:
            result["model_display"] = m.group(1).strip()
            continue

        m = _SIDEBAR_CWD_RE.match(side)
        if m:
            result["cwd_display"] = m.group(1).strip()
            continue

        m = _SIDEBAR_TITLE_RE.match(side)
        if m and not _SIDEBAR_LABEL_RE.match(side):
            candidate = m.group(1).strip()
            # 标题是侧边栏第一个非标签行；排除 logo 行
            if candidate and "Charm" not in candidate and not result["title"]:
                result["title"] = candidate

    return result


def parse_screen_lines(lines: List[str]) -> LiveState:
    """从已解析的屏幕行列表提取 LiveState。

    Args:
        lines: 屏幕各行文本（pyte 解析后，不 rstrip）
    """
    state = LiveState()

    if not lines:
        return state

    joined = "\n".join(lines)

    # 界面类型：欢迎页 vs 对话中
    has_welcome = _WELCOME_LOGO_RE.search(joined) is not None
    has_conversation = any(kw in joined for kw in _CONVERSATION_MARKS)
    state.screen_type = (
        "conversation" if (has_conversation and not has_welcome) else
        ("main" if has_welcome else "conversation")
    )

    # AI 状态（先检测：question 表单/权限弹窗时输入框语义不同）
    state.ai_status = _detect_ai_status(lines)

    # 输入框文字（question 表单下 `> Something else?` 是选项，非输入）
    if state.ai_status == "awaiting_answer":
        state.input_text = ""
    else:
        state.input_text = _extract_input_text(lines)

    # 侧边栏字段
    side = _extract_sidebar(lines)
    state.title = side["title"]
    state.cwd_display = side["cwd_display"]
    state.model_display = side["model_display"]
    state.context_percent = side["context_percent"]
    state.context_tokens = side["context_tokens"]
    state.cost_display = side["cost_display"]
    state.version_display = side["version_display"]

    # 模型元数据行兜底（欢迎页无侧边栏时）
    if not state.model_display:
        for line in lines:
            m = _MODEL_META_RE.search(line)
            if m:
                state.model_display = m.group(1).strip()
                break

    # 版本号兜底
    if not state.version_display:
        m = _VERSION_RE.search(joined)
        if m:
            state.version_display = m.group(1)

    _log.debug("parse_screen_lines: status=%s ctx=%s%% input=%r screen=%s model=%r title=%r",
               state.ai_status, state.context_percent, state.input_text,
               state.screen_type, state.model_display, state.title)
    return state


def parse_screen_snapshot(vt_text: str, columns: int = 0, rows: int = 0) -> LiveState:
    """从带 VT 序列的屏幕全量文本提取 LiveState。

    Args:
        vt_text: PTY-Agent --keep-ansi 输出
        columns: 指定列数，0 则自动检测
        rows: 指定行数，0 则自动检测

    Returns:
        LiveState 实体
    """
    lines = parse_screen(vt_text, columns=columns, rows=rows, rstrip=False)
    return parse_screen_lines(lines)