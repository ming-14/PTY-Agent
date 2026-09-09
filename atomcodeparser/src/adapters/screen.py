"""屏幕快照解析适配器：将 VT 终端快照解析为 LiveState。

AtomCode TUI 布局（v5.0.8，实测）：

欢迎页（main）：
```
  * AtomCode                            v5.0.8  ·  MIT
  * ~/Desktop/atomcodeparser
  * sensenova-6.8-flash-lite
  Tips for getting started
  /login  claim a free token quota
  ...
    i Multi-line input: ...
────────────────────────────────────
>
────────────────────────────────────
  sensenova-6.8-flash-lite · ~/Desktop/atomcodeparser · 0/262k tok (0%)
```

对话中（conversation）：
```
> hi

  Hi! How can I help you today?

  --- Done · 1 rounds · 0 tools · 6.5s · 859 tokens · 95% cached ---

> 看看当前目录

* ListDirectory(C:/Users/<user>/Desktop/atomcodeparser)
  ` hash_crack.py (13 lines)

  看起来是一个密码哈希相关的 Python 项目...

  --- Nailed it · 2 rounds · 1 tools · 6.9s · 18.56K tokens · 47% cached ---

────────────────────────────────────
>
────────────────────────────────────
  sensenova-6.8-flash-lite · ~/Desktop/atomcodeparser · 17.4k/262k tok (7%)
```

思考状态：`/ Noodling... (0s)` 或 `- Pondering... (4s)`
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..entities import LiveState
from ..infra.logging import get_logger
from ..infra.vt import parse_screen

_log = get_logger("screen")

# ──────────────────────────────────────────
# 布局特征正则
# ──────────────────────────────────────────

# 欢迎页 header：`  * AtomCode ... v5.0.8 · MIT`
_WELCOME_HEADER_RE = re.compile(r"^\s*\*\s*AtomCode\b")
# 版本号：`v5.0.8`（header 行内）
_VERSION_RE = re.compile(r"\bv(\d+\.\d+\.\d+)\b")

# 状态栏模型名搜索（无 ^ 锚定，可匹配合并后含前导残留的行）
_STATUS_MODEL_SEARCH_RE = re.compile(r"([\w.+-]+(?:\/[\w.+-]+)?)\s*·")
# 上下文占用：`17.4k/262k tok (7%)` 或 `0/262k tok (0%)`（数值可带 k/M 后缀）
_CONTEXT_RE = re.compile(r"(\d[\d,.]*[kKmM]?)\s*/\s*(\d[\d,.]*[kKmM]?)\s*tok\s*\((\d+)%\)")
# 百分比单独：`(N%)`
_PERCENT_RE = re.compile(r"\((\d+)%\)")

# 思考状态
_THINKING_RE = re.compile(r"^\s*[/-]\s*(?:Noodling|Pondering)\.{3}")

# 输入框：分隔线（纯 ─ 行）之后紧跟 `>` 行
_INPUT_LINE_RE = re.compile(r"^\s*>\s?(.*)$")
# 分隔线：整行纯 ─（10 个以上）
_SEPARATOR_RE = re.compile(r"^\s*─{10,}\s*$")

# 工具执行行：`* ListDirectory(...)` / `* Read(...)` 等
_TOOL_LINE_RE = re.compile(r"^\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# ──────────────────────────────────────────
# 提问（request_user_input）状态特征
# ──────────────────────────────────────────

# 工具行：`* RequestUserInput`
_ASK_TOOL_RE = re.compile(r"^\s*\*\s*RequestUserInput\b", re.IGNORECASE)
# 导航提示：`^v move · 1-3 select · Enter confirm · Esc cancel`
#（窄屏可能截断：`^v move · 1-3 select · Enter conf…`）
_ASK_NAV_RE = re.compile(r"^\s*\^v\s*move\s*·.*select\b", re.IGNORECASE)
# 选中选项行：`> 1. hash_crack`（`>` 前缀 + 数字 + `.`）
_ASK_SELECTED_RE = re.compile(r"^\s*>\s*\d+\s*\.\s")
# 未选中选项行：`  2. crack_kit`（行首数字 + `.` + 空格 + 非数字内容）
_ASK_OPTION_RE = re.compile(r"^\s*\d+\s*\.\s")
# 自定义答案选项：`3. 输入自己的答案…` / `Type your own answer`
_ASK_CUSTOM_RE = re.compile(r"(输入自己的答案|Type your own|type your own)", re.IGNORECASE)

# 回合完成标记：`v Done · 1 rounds ...` 或 `--- v Done · 1 rounds ...`
_TURN_DONE_RE = re.compile(r"^\s*(?:[-─]+\s*)?(?:[vx!✓✗]\s*)?(?:Done|Nailed it|Failed|Error|Cancelled|Interrupted)\b")


def _is_asking(lines: List[str]) -> bool:
    """判定是否为提问状态（request_user_input 弹窗）。

    特征（任一即可）：
    - `* RequestUserInput` 工具行
    - `^v move · N-N select · Enter confirm` 导航提示
    - `> N. xxx` 选中选项 + 自定义答案选项
    """
    if any(_ASK_TOOL_RE.match(line) for line in lines):
        return True
    if any(_ASK_NAV_RE.match(line) for line in lines):
        return True
    # 选中选项 + 未选中选项同时出现（且含自定义答案项）→ 提问弹窗
    has_selected = any(_ASK_SELECTED_RE.match(line) for line in lines)
    has_option = sum(1 for line in lines if _ASK_OPTION_RE.match(line)) >= 2
    has_custom = any(_ASK_CUSTOM_RE.search(line) for line in lines)
    if has_selected and has_option and has_custom:
        return True
    return False


def _is_welcome(lines: List[str]) -> bool:
    """判定是否为欢迎页（main）。"""
    return any(_WELCOME_HEADER_RE.match(line) for line in lines)


def _is_conversation(lines: List[str]) -> bool:
    """判定是否为对话中（有回合完成标记 / 思考状态 / 工具执行行 / 提问弹窗 / 输入框+分隔线）。"""
    if any(_TURN_DONE_RE.match(line) for line in lines):
        return True
    if any(_THINKING_RE.match(line) for line in lines):
        return True
    if any(_TOOL_LINE_RE.match(line) for line in lines):
        return True
    if _is_asking(lines):
        return True
    # 兜底：有分隔线且有 `>` 输入行
    has_sep = any(_SEPARATOR_RE.match(line) for line in lines)
    has_input = any(_INPUT_LINE_RE.match(line) for line in lines)
    if has_sep and has_input:
        return True
    return False


def _is_status_like(line: str) -> bool:
    """判断一行是否可能为状态栏（含 token 信息 / 路径 / 模型名·目录结构）。"""
    if _CONTEXT_RE.search(line):
        return True
    if "·" not in line:
        return False
    # 排除回合完成标记、工具行等非状态栏内容
    if _TURN_DONE_RE.match(line) or _TOOL_LINE_RE.match(line) or _THINKING_RE.match(line):
        return False
    # 含路径特征（~ / :\ / tok / %）
    if "~" in line or re.search(r"[A-Za-z]:[\\/]", line):
        return True
    if re.search(r"tok\b", line, re.IGNORECASE) or "%" in line:
        return True
    # 模型名 · 目录 形态：行首为合法模型名
    m = _STATUS_MODEL_SEARCH_RE.search(line)
    if m and m.group(1).lower() not in _STATUS_STOP_WORDS:
        return True
    return False


# 不应被误认为状态栏模型名的词
_STATUS_STOP_WORDS = frozenset((
    "rounds", "tools", "tokens", "done", "nailed", "failed", "error",
    "cancel", "interrupt", "tip", "tips", "tipsy", "move", "select",
    "enter", "confirm", "esc", "lines", "hidden", "pgup", "pgdn",
))


def _find_status_bar(lines: List[str]) -> Optional[str]:
    """查找状态栏（从屏幕底部向上搜索，处理跨行换行与分隔线残留）。

    Returns:
        合并后的状态栏文本（含模型名、目录、token），或 None
    """
    # 从底部向上找状态栏候选行（含 token 信息或模型名·目录结构）
    status_idx = None
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if not line:
            continue
        if _is_status_like(line):
            status_idx = i
            break

    if status_idx is None:
        return None

    # 向上合并跨行换行的状态栏片段
    parts = [lines[status_idx]]
    for j in range(1, 5):
        if status_idx - j < 0:
            break
        line = lines[status_idx - j].rstrip()
        if not line.strip() or _SEPARATOR_RE.match(line):
            break
        # 该行含 `·` 且像状态栏内容（模型名/路径/token）→ 合并
        if "·" in line and ("~" in line or "tok" in line.lower()
                            or re.search(r"[A-Za-z]:[\\/]", line)
                            or re.search(r"\b[\w.+-]+(?:\/[\w.+-]+)?\s*·", line)):
            parts.insert(0, line)
        else:
            break

    merged = " ".join(p.strip() for p in parts)
    return merged


def _find_input_line(lines: List[str]) -> str:
    """查找输入框文字（分隔线后的 `>` 行）。

    Returns:
        输入框文字（空串表示无输入或 placeholder）
    """
    for i, line in enumerate(lines):
        if not _SEPARATOR_RE.match(line):
            continue
        # 分隔线之后的行可能是 `>` 输入行
        for j in range(i + 1, min(i + 3, len(lines))):
            m = _INPUT_LINE_RE.match(lines[j])
            if m:
                text = m.group(1).strip()
                # 排除 placeholder 和纯装饰字符（分隔线残留）
                if not text or text in _PLACEHOLDERS:
                    return ""
                # 过滤纯分隔符/装饰字符（增量刷新残留）
                if re.match(r"^[─━═─\s]+$", text):
                    return ""
                return text
            # 若下一行不是输入行且不是空白/分隔线，停止
            if lines[j].strip() and not _SEPARATOR_RE.match(lines[j]):
                break
    return ""


# 已知 placeholder（输入框为空时的提示文字）
_PLACEHOLDERS = frozenset((
    "Ask anything...",
    "Type a message...",
    "Send a message...",
    "Enter your message...",
    "Ask anything",
    "Type your message",
))


def parse_screen_snapshot(vt_text: str, columns: int = 0, rows: int = 0) -> LiveState:
    """解析终端屏幕快照为 LiveState。

    Args:
        vt_text: PTY-Agent --keep-ansi 输出（含 VT 序列）
        columns: 指定列数，0 则自动检测
        rows: 指定行数，0 则自动检测

    Returns:
        LiveState
    """
    lines = parse_screen(vt_text, columns, rows)
    _log.debug("screen lines: %d", len(lines))

    state = LiveState()

    # 界面类型（欢迎页优先判定，避免分隔线误判；提问弹窗不算欢迎页）
    if _is_asking(lines):
        state.screen_type = "conversation"
    elif _is_welcome(lines) and not any(_THINKING_RE.match(l) for l in lines) \
            and not any(_TURN_DONE_RE.match(l) for l in lines):
        state.screen_type = "main"
    elif _is_conversation(lines):
        state.screen_type = "conversation"
    else:
        state.screen_type = ""

    # AI 状态（优先级：提问 > 思考 > 工具执行 > 空闲）
    if _is_asking(lines):
        state.ai_status = "awaiting_answer"
    elif any(_THINKING_RE.match(l) for l in lines):
        state.ai_status = "thinking"
    elif any(_TOOL_LINE_RE.match(l) for l in lines):
        state.ai_status = "tool_running"
    else:
        state.ai_status = "idle"

    # 输入框文字
    state.input_text = _find_input_line(lines)

    # 版本号（欢迎页 header）
    for line in lines:
        if _WELCOME_HEADER_RE.match(line):
            m = _VERSION_RE.search(line)
            if m:
                state.version_display = m.group(1)
            break

    # 状态栏：模型名 / 工作目录 / 上下文
    status = _find_status_bar(lines)
    if status:
        # 从状态栏中提取模型名（search 而非 match，因合并后可能有前导残留）
        m = _STATUS_MODEL_SEARCH_RE.search(status)
        if m:
            state.model_display = m.group(1)
        cm = _CONTEXT_RE.search(status)
        if cm:
            state.context_tokens = _parse_int(cm.group(1))
            state.context_window = _parse_int(cm.group(2))
            state.context_percent = _parse_float(cm.group(3))
        else:
            pm = _PERCENT_RE.search(status)
            if pm:
                state.context_percent = _parse_float(pm.group(1))
        # 工作目录：`· ~/Desktop/...` 或 `· .../ato` 段（跳过 token 信息段与导航提示段）
        # 跨行合并时路径可能带空格，先去空格
        parts = [p.strip() for p in status.split("·") if p.strip()]
        for part in parts[1:]:
            # 跳过 token 信息段（含 tok / % / 数字比例）
            if re.search(r"tok\b", part, re.IGNORECASE) or "%" in part:
                continue
            # 跳过纯数字/token 比例段
            if re.match(r"^[\d\s.,kKmM%()/]+$", part):
                continue
            # 跳过导航提示段（move / select / Enter confirm / Esc cancel / PgUp/PgDn / hidden lines）
            if re.search(r"\b(move|select|enter|confirm|esc|cancel|pgup|pgdn|hidden)\b", part, re.IGNORECASE):
                continue
            # 跳过纯数字+符号段（如 `1-3`）
            if re.match(r"^[\d\s\-]+$", part):
                continue
            # 含路径形态（~、/、\、盘符）或相对目录名
            if "~" in part or "/" in part or "\\" in part or re.match(r"^[A-Za-z]:", part):
                # 跨行合并的路径片段去掉内部空格（~/Desktop/ atomcodeparser → ~/Desktop/atomcodeparser）
                state.cwd_display = part.replace(" ", "")
                break
            # 非路径形态但非 token 信息的片段（如截断的目录名）
            if part and len(part) > 1:
                state.cwd_display = part
                break

    # 兜底：欢迎页 `* ~/Desktop/...` 行
    if not state.cwd_display:
        for line in lines:
            m = re.match(r"^\s*\*\s+(~[^\s]+|[A-Za-z]:[^\s]+)", line)
            if m:
                state.cwd_display = m.group(1)
                break

    return state


def _parse_int(s: str) -> int:
    """解析可能含千位分隔符和 k/M 后缀的整数。

    - `17.4k` → 17400
    - `262k` → 262000
    - `1,000` → 1000
    - `0` → 0
    """
    s = s.strip().replace(",", "")
    multiplier = 1
    if s.endswith("k") or s.endswith("K"):
        multiplier = 1000
        s = s[:-1]
    elif s.endswith("m") or s.endswith("M"):
        multiplier = 1000000
        s = s[:-1]
    try:
        return int(float(s) * multiplier)
    except ValueError:
        return 0


def _parse_float(s: str) -> float:
    """解析浮点数。"""
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return 0.0