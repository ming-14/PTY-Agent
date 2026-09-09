r"""屏幕快照解析适配器：从 Pi TUI 屏幕提取实时状态（LiveState）。

Pi TUI 布局（v0.84.2，实测）：

欢迎页（main）：
```
pi v0.84.2
escape interrupt · ctrl+c/ctrl+d clear/exit · / commands · ! bash · ctrl+o more
Press ctrl+o to show full startup help and loaded resources.

Pi can explain its own features and look up its docs. Ask it how to use or extend Pi.


[Context]
  ~\Desktop\AGENTS.md

──────────────────────────────────────────────────────────────────────
▌(光标,反色块)
──────────────────────────────────────────────────────────────────────
~\Desktop\piparser
0.0%/262k (auto)                                          sensenova-6.8-flash-lite • high
```

对话中（conversation）：
```
pi v0.84.2
...
[Context]
  ~\Desktop\AGENTS.md

（用户消息块，深色背景 48;2;52;53;65）
 你好,请介绍一下你自己
（assistant 正文）
 你好 <username>！
（thinking，斜体灰）
 The user wants me to list...
（工具执行块，深绿背景 48;2;40;50;40）
 $ dir

 launch_pi.cmd

 Took 5.8s
（工作中状态行）
 ⠋ Working...
──────────────────────────────────────────────────────────────────────
▌(输入框,反色块=光标)
──────────────────────────────────────────────────────────────────────
~\Desktop\piparser
↑8.1k ↓360 1.1%/262k (auto)                              sensenova-6.8-flash-lite • high
```

解析策略：从底部往上定位 footer（两行：cwd 行 + stats 行），
再向上定位输入框行（分隔线之间），分段正则提取各字段。
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..entities import LiveState
from ..infra.logging import get_logger
from ..infra.vt import parse_screen

_log = get_logger("screen")

# ── 正则 ──

# 版本 header：pi v0.84.2
_VERSION_RE = re.compile(r"^\s*pi\s+v(\d+\.\d+\.\d+)")

# 欢迎页关键词（main 判定）
_WELCOME_KEYWORDS = (
    "Press ctrl+o to show full startup help",
    "Pi can explain its own features",
    "[Context]",
)

# 工作中 spinner：⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ Working...
_WORKING_RE = re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s*(Working|Thinking|Compacting|Retrying)\.{3}")

# 工具执行块：$ cmd（深绿背景区域内的命令行）
_TOOL_CMD_RE = re.compile(r"^\s*\$ (.+)$")

# 工具完成标记：Took 5.8s
_TOOL_TOOK_RE = re.compile(r"Took\s+[\d.]+s")

# 底部分隔线：连续 ─（紫色）
_SEPARATOR_RE = re.compile(r"^─{10,}$")

# footer cwd 行：~\path 或 C:\path（~ 缩写用户目录）
_CWD_DISPLAY_RE = re.compile(r"^(~[/\\][^•|]*|[A-Za-z]:[/\\][^•|]*)")

# footer stats：token 统计 ↑N ↓N（支持 k/M 缩写）
_TOKENS_RE = re.compile(r"↑([\d.]+[kM]?)\s+↓([\d.]+[kM]?)")

# 上下文：N%/Nk (auto)
_CONTEXT_RE = re.compile(r"(\d+(?:\.\d+)?)%/(\d+(?:\.\d+)?[kK]?)(?:\s*\(auto\))?")

# 模型 + 思考等级（footer 右侧）：model • thinking
_MODEL_THINKING_RE = re.compile(r"([^\s•]+(?:\s*/\s*[^\s•]+)?)\s*•\s*(off|minimal|low|medium|high|xhigh|max|thinking off)")

# 费用：$N
_COST_RE = re.compile(r"\$([\d.]+)")

# 用户消息块（深色背景 48;2;52;53;65 的行）
_USER_MSG_BG = "48;2;52;53;65"

# 工具执行块（深绿背景 48;2;40;50;40）
_TOOL_BG = "48;2;40;50;40"

# 光标标记（反色半块/整块，输入框行前缀）
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

    non_empty = [i for i, l in enumerate(lines) if l.strip()]
    if not non_empty:
        return state
    last = non_empty[-1]

    # ── footer：最后两行（cwd 行 + stats 行）──
    # 从底部向上找 stats 行（含 ↑/↓ 或 %/ 或 model •）
    stats_idx: Optional[int] = None
    for i in range(last, max(-1, last - 6), -1):
        if _is_stats_line(lines[i]):
            stats_idx = i
            break

    if stats_idx is not None and stats_idx - 1 >= 0:
        cwd_line = lines[stats_idx - 1].strip()
        m = _CWD_DISPLAY_RE.match(cwd_line)
        if m:
            state.cwd_display = m.group(1).strip()

    # ── footer stats 字段 ──
    if stats_idx is not None:
        _parse_stats_line(lines[stats_idx], state)

    # ── 屏幕类型 ──
    state.screen_type = "main" if _is_welcome(lines) else "conversation"

    # ── AI 状态 ──
    state.ai_status = _detect_ai_status(lines)

    # ── 输入框文字 ──
    state.input_text = _extract_input_text(lines, stats_idx)

    _log.debug("live_state: status=%s screen=%s ctx=%.1f%% model=%s thinking=%s",
               state.ai_status, state.screen_type, state.context_percent,
               state.model_display, state.thinking_level)
    return state


def _is_stats_line(line: str) -> bool:
    """判断是否为 footer stats 行。

    特征：含 ↑/↓ token、%/ 上下文、或 model • thinking。
    """
    return bool(
        _TOKENS_RE.search(line)
        or _CONTEXT_RE.search(line)
        or _MODEL_THINKING_RE.search(line)
    )


def _parse_stats_line(line: str, state: LiveState) -> None:
    """从 stats 行提取 token / context / model / thinking 字段。"""
    m = _TOKENS_RE.search(line)
    if m:
        state.input_text = state.input_text  # noop 占位，token 不放入 input_text

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

    m = _MODEL_THINKING_RE.search(line)
    if m:
        state.model_display = m.group(1).strip()
        level = m.group(2).strip()
        if level == "thinking off":
            state.thinking_level = "off"
        else:
            state.thinking_level = level


def _is_welcome(lines: List[str]) -> bool:
    """判断是否欢迎页（main）。"""
    joined = "\n".join(lines)
    return any(kw in joined for kw in _WELCOME_KEYWORDS)


def _detect_ai_status(lines: List[str]) -> str:
    """从屏幕内容检测 AI 状态。

    优先级：asking（选择器/对话框）> 权限请求 > 工具执行中 > 工作中 > 空闲
    """
    joined = "\n".join(lines)

    # asking：选择器/对话框（扩展 confirm/input/select、settings、tree 等）
    # 特征：→ 选中标记选项 / 帮助行 / 滚动指示 (N/M)
    if _is_asking(lines):
        return "asking"

    # 权限请求（Pi 无内置权限系统，但扩展可能注入）
    if "Permission" in joined and ("Allow" in joined or "approve" in joined.lower()):
        return "awaiting_approval"

    # 工作中 spinner
    if _WORKING_RE.search(joined):
        # 若同时存在未完成的工具块（$ cmd 后无 Took）→ tool_running
        if _has_running_tool(lines):
            return "tool_running"
        return "working"

    # 工具块且无 Took（刚启动，spinner 帧间）→ tool_running
    if _has_running_tool(lines):
        return "tool_running"

    return "idle"


def _is_asking(lines: List[str]) -> bool:
    """检测是否处于 asking 状态（选择器/对话框）。

    Pi 的选择器（settings / tree / 扩展 select / confirm / input）特征：
    - `→` 前缀的选中选项（最常见、最独特）
    - 帮助行：`Enter/Space to change` / `↑↓ navigate` / `submit  cancel`
    - 滚动指示：(N/M)
    - 搜索框：`> ` 前缀 + 反色光标（在蓝色边框内）
    """
    joined = "\n".join(lines)

    # 选中项标记：→ 前缀（排除 footer 的 → 符号，限定行首）
    if re.search(r"^\s*→\s+\S", joined, re.MULTILINE):
        return True

    # 帮助行
    if any(kw in joined for kw in (
        "Enter/Space to change",
        "↑↓ navigate",
        "submit  cancel",
        "Type to search",
    )):
        return True

    # 滚动指示：(N/M) 行（如 (1/29)）
    if re.search(r"^\s*\(\d+/\d+\)\s*$", joined, re.MULTILINE):
        return True

    return False


def _has_running_tool(lines: List[str]) -> bool:
    """检测是否存在进行中的工具执行块。

    特征：$ cmd 行存在，且其下方无对应的 Took Ns 完成标记。
    """
    tool_cmd_idx = None
    for i, l in enumerate(lines):
        if _TOOL_CMD_RE.match(l):
            tool_cmd_idx = i
    if tool_cmd_idx is None:
        return False
    # 检查工具块之后是否有 Took（工具块与 Took 之间允许输出行）
    for l in lines[tool_cmd_idx:]:
        if _TOOL_TOOK_RE.search(l):
            return False
    return True


def _extract_input_text(lines: List[str], stats_idx: Optional[int]) -> str:
    """提取输入框文字。

    Pi 的输入框在两条分隔线之间（底部上方），光标为反色块；
    输入文字显示在输入框行。欢迎页输入框通常为空。
    """
    if stats_idx is None:
        return ""
    # 向上找输入框行（stats 行上方第 2 行起，含两条分隔线）
    for i in range(stats_idx - 1, max(-1, stats_idx - 8), -1):
        line = lines[i]
        if _SEPARATOR_RE.match(line.strip()):
            # 分隔线上方一行即输入框行
            if i - 1 >= 0:
                input_line = lines[i - 1]
                # 去掉反色块光标标记（▌/█ 半块或整块）
                text = _CURSOR_RE.sub("", input_line).strip()
                if not text:
                    return ""
                return text
    return ""
