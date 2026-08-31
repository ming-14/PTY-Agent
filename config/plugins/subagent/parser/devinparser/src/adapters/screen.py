"""屏幕快照解析适配器：从 Devin TUI 屏幕提取实时状态（LiveState）。

Devin CLI TUI 布局（v3000.4.16，实测）：
```
[Logo 区：⠀⣴⣾⣶⡄ / Devin CLI / v3000.4.16]     ← 欢迎页
[Free plan, use /upgrade ... 100% remaining]       ← 配额行
[消息区（含 scrollback）]
❭ <用户输入>                                      ← 用户消息回显（❭ 前缀）
<AI 回复>                                          ← 助手回复（无前缀）
● Running command / ● Ran command                 ← 工具执行
  └ <输出> / └ Exited with code 0
⠦⠀ Thinking · Ns (esc twice to interrupt) ...     ← 思考中（Braille 旋转 + 秒数）
[权限请求框]
❭ 1 Yes  (Approve once)                            ← 权限选项（❭ 选中项 / · 未选中）
· 2 Yes, allow ...
↑↓ select · ↵ confirm · esc cancel                 ← 导航提示
──────────────────────────────────────────────────  ← 分隔线
❭ Ask Devin to build features, fix bugs, ...       ← 输入框（placeholder 空闲）
  / ❭ Guide Devin while it works                   ← 输入框（工作中提示）
──────────────────────────────────────────────────
SWE-1.6 Slow ... Context: 13k / 200k tokens (6%)   ← 状态栏
⚠︎ Unsupported terminal ...                        ← 警告/更新横幅（可有可无）
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

# 分隔线：整行几乎全为 ─（消息区/输入框/状态栏分隔）
_SEPARATOR_RE = re.compile(r"^\s*─{10,}\s*$")

# 输入框：❭ 后文本（提示符后跟文本）
_INPUT_RE = re.compile(r"❭\s?(.*)")

# 权限选项行：❭ 1. Yes / · 2. ...（选中/未选中选项）
_OPTION_RE = re.compile(r"^(?:❭|·)\s*\d+\.")

# 思考中：⠦⠀ Thinking · Ns (esc twice to interrupt)
_THINKING_RE = re.compile(r"Thinking\s*·\s*\d+s")

# 工具执行中（进行时）：● Running
_TOOL_RUNNING_RE = re.compile(r"●\s*Running\b")

# 工具执行完成：● Ran / ● Updated / ● Read / ● Found
_TOOL_DONE_RE = re.compile(r"●\s*(?:Ran|Updated|Read|Found|Wrote|Created)")

# 上下文状态栏：Context: 13k / 200k tokens (6%)
_CONTEXT_RE = re.compile(r"Context:\s*([\d.]+[kKmM]?)\s*/\s*([\d.]+[kKmM]?)\s*tokens?\s*\((\d+)%\)")

# 模型名：状态栏行首（如 SWE-1.6 Slow）
_MODEL_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9\-./ ]{2,60}?)\s{2,}")

# 权限请求关键词（权限框导航提示 / 选项文本）
_PERMISSION_KEYWORDS = (
    "↑↓ select · ↵ confirm · esc cancel",
    "Approve once",
    "Yes, allow",
)

# 提问框关键词（ask_user_question 模态框）
_ASKING_KEYWORDS = (
    "↑↓ navigate · ↵ select",
    "switch question",
    "? help me out",
    "? Not ready to answer",
    "Other (type your own)",
)

# 拒绝标记（工具执行被用户拒绝）
_DENIED_RE = re.compile(r"✗\s*(?:Tool execution was rejected|You (?:canceled|cancelled))")

# 思考中关键词（进行中）
_THINKING_KEYWORDS = ("Thinking ·", "(esc twice to interrupt)")

# 工作中输入框 placeholder
_PLACEHOLDER_WORKING = "Guide Devin while it works"
_PLACEHOLDER_MAIN = "Ask Devin to build features, fix bugs, or work on your code"

# 旋转动画字符集（Braille）
_SPINNER_CHARS = frozenset("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⠿⣄⣠⣴⣾⣶⡄")


def _detect_ai_status(lines: List[str]) -> str:
    """从消息区检测 AI 状态。

    Returns:
        idle / thinking / tool_running / awaiting_approval / asking
        - asking: AI 提问框（ask_user_question，等待用户选择）
        - awaiting_approval: 权限请求框（等待用户批准工具）
    """
    scan_text = "\n".join(lines)

    # 提问框优先（导航提示含 "↑↓ navigate" / "switch question"）
    for kw in _ASKING_KEYWORDS:
        if kw in scan_text:
            return "asking"

    for kw in _PERMISSION_KEYWORDS:
        if kw in scan_text:
            return "awaiting_approval"

    if _THINKING_RE.search(scan_text) or any(kw in scan_text for kw in _THINKING_KEYWORDS):
        return "thinking"

    if _TOOL_RUNNING_RE.search(scan_text):
        return "tool_running"

    # 旋转动画：只检测消息区（排除顶部 logo 区的静态 Braille 字符）
    # Devin 欢迎页 logo 区约前 5 行含 ⣴⣾⣶⡄ 等静态字符，不表示工具执行中
    message_lines = lines[5:]
    for line in message_lines:
        if any(ch in _SPINNER_CHARS for ch in line):
            return "tool_running"

    return "idle"


def _find_input_and_status(lines: List[str]) -> tuple:
    """定位输入框和状态栏，返回输入框行索引。

    Devin TUI 布局（分隔线分割）：
    ```
    ──────────── 分隔线1（可选）
    ❭ 输入框
    ──────────── 分隔线2
    SWE-1.6 Slow ... Context: ...   ← 状态栏（分隔线2 之下）
    ```

    策略：
    1. 从底部往上找第一条分隔线 sep1 → 其下所有非空行 = 状态栏
    2. 在 sep1 之上找第二条分隔线 sep2 → sep2 与 sep1 之间的 `❭` 行 = 输入框
    3. 若没有 sep2，sep1 之上最近的 `❭` 行 = 输入框

    Returns:
        (input_text, bar_lines, input_idx)
        input_text: 输入框文字（placeholder 时返回空字符串）
        bar_lines: 状态栏非空行列表
        input_idx: 输入框行索引（-1 如果未找到）
    """
    input_text = ""
    bar_lines: List[str] = []
    input_idx = -1

    # 1. 找到所有分隔线索引（从下到上）
    sep_idxs = [i for i, l in enumerate(lines) if _SEPARATOR_RE.search(l)]

    if not sep_idxs:
        return input_text, bar_lines, input_idx

    # 最后一条分隔线 → 其下为状态栏
    last_sep = sep_idxs[-1]
    for j in range(last_sep + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped:
            bar_lines.append(stripped)

    # 2. 状态栏上方的分隔线（倒数第二条，可选）
    upper_sep = sep_idxs[-2] if len(sep_idxs) >= 2 else -1

    # 3. 输入框：upper_sep 之后、last_sep 之前的最后一个 `❭` 行
    search_start = upper_sep + 1 if upper_sep >= 0 else 0
    for i in range(last_sep - 1, search_start - 1, -1):
        m = _INPUT_RE.match(lines[i])
        if m:
            text = m.group(1).strip()
            # 过滤纯分隔符残留（窄屏/渲染重叠时输入框行可能混入 ─）
            if text:
                text = re.sub(r"[─━]+$", "", text).rstrip()
            if text:
                # 过滤 placeholder
                if text == _PLACEHOLDER_MAIN or text == _PLACEHOLDER_WORKING:
                    input_text = ""
                else:
                    input_text = text
                input_idx = i
            break

    # 无输入框（模态框覆盖输入区）时，分隔线之下的行不是真实状态栏
    # （如提问框/权限框的导航提示行），清空避免误解析
    if input_idx < 0:
        bar_lines = []

    return input_text, bar_lines, input_idx


def _parse_status_bar(bar_lines: List[str]) -> dict:
    """从状态栏行提取 model / context_percent。"""
    result = {
        "model_display": "",
        "context_percent": 0.0,
        "context_used": "",
        "context_total": "",
    }

    # 合并所有行文本用于分段搜索
    merged = " ".join(line.strip() for line in bar_lines)

    # 上下文百分比
    m = _CONTEXT_RE.search(merged)
    if m:
        used, total, pct = m.groups()
        result["context_percent"] = float(pct)
        result["context_used"] = used
        result["context_total"] = total

    # 模型名：状态栏行首（两个空格以上分隔模型名与右侧内容）
    if bar_lines:
        m = _MODEL_RE.match(bar_lines[0].strip())
        if m:
            result["model_display"] = m.group(1).strip()
        elif not result["model_display"]:
            # 兜底：取状态栏行首 20 字符（排除快捷键提示）
            first = bar_lines[0].strip()
            if first and not first.startswith("Press"):
                result["model_display"] = first.split("  ")[0][:30]

    return result


def parse_screen_lines(lines: List[str]) -> LiveState:
    """从已解析的屏幕行列表提取 LiveState。

    Args:
        lines: 屏幕各行文本（pyte 解析后，不 rstrip）

    Returns:
        LiveState 实体
    """
    state = LiveState()

    if not lines:
        return state

    # 输入框 + 状态栏（先定位，用于界面类型判定）
    input_text, bar_lines, input_idx = _find_input_and_status(lines)
    state.input_text = input_text

    # 界面类型：欢迎页（只有 logo + placeholder）vs 对话中（消息区有内容）
    # 判定依据：输入框上方是否有对话内容（用户消息回显 / AI 回复 / 工具行）
    has_conversation = False
    if input_idx > 0:
        # 消息区 = 输入框以上的行（排除 logo 区前 5 行与空行）
        for line in lines[:input_idx]:
            s = line.strip()
            if not s:
                continue
            # logo 区（前 5 行含 Braille 字符）与配额行不算对话内容
            if s.startswith("Devin CLI") or "v3000." in s:
                continue
            if s.startswith("Free plan") or s.startswith("⠀⣴") or s.startswith("⠀⠛"):
                continue
            # 权限框 / 提问框 / 思考 / 工具行 / 拒绝标记 / 用户回显 / AI 回复 → 对话中
            if (any(kw in s for kw in _PERMISSION_KEYWORDS)
                    or any(kw in s for kw in _ASKING_KEYWORDS)
                    or _THINKING_RE.search(s)
                    or _TOOL_DONE_RE.search(s)
                    or _DENIED_RE.search(s)
                    or _INPUT_RE.match(s)
                    or "Thinking ·" in s):
                has_conversation = True
                break
    # 兜底：对话标记关键词
    joined = "\n".join(lines)
    if not has_conversation:
        has_conversation = (
            any(kw in joined for kw in _PERMISSION_KEYWORDS)
            or any(kw in joined for kw in _ASKING_KEYWORDS)
            or _THINKING_RE.search(joined) is not None
            or _TOOL_DONE_RE.search(joined) is not None
            or _DENIED_RE.search(joined) is not None
            or "•" in joined or "●" in joined
        )

    state.screen_type = "conversation" if has_conversation else "main"

    # 状态栏字段
    bar_info = _parse_status_bar(bar_lines)
    state.model_display = bar_info["model_display"]
    state.context_percent = bar_info["context_percent"]

    # AI 状态
    state.ai_status = _detect_ai_status(lines)

    _log.debug("parse_screen_lines: status=%s ctx=%s%% input=%r screen=%s model=%r",
               state.ai_status, state.context_percent, state.input_text,
               state.screen_type, state.model_display)
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