"""屏幕快照解析适配器：从 opencode TUI 屏幕提取实时状态（LiveState）。

opencode TUI 布局（v1.18.21，实测）：

欢迎页（main）：
```
                                                       ▄
                     █▀▀█ █▀▀█ █▀▀█ █▀▀▄ █▀▀▀ █▀▀█ █▀▀█ █▀▀█
                     █  █ █  █ █▀▀▀ █  █ █    █  █ █  █ █▀▀▀
                     ▀▀▀▀ █▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀
  ┃
  ┃  Ask anything... "Fix broken tests"
  ┃
  ┃  Build · Ox Alpha Free (Unlimited) OpenCode Zen · max
  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
  tab agents  ctrl+p commands
  ~/Desktop/opencodeparser                                1.18.21
```

对话中（conversation）：
```
  ┃  你好，请简单介绍一下你自己
                                              Context
  + Thought: 2.9s                             10,721 tokens
                                               1% used
  你好，alice！我是 opencode...                $0.00 spent
                                               LSP
  ┃                                            LSPs are disabled
  ┃  请用 dir 命令列出当前目录内容

  ┃  $ dir
  ┃  Directory: ...
  [工具输出]
  ┃  Build · Ox Alpha Free (Unlimited) OpenCode Zen · max
  ╹▀▀▀▀... (separator)
  C:/Users/alice/Desktop/opencodeparser  11.0K (1%)  ctrl+p commands    • OpenCode 1.18.21
```

权限请求（awaiting_approval）：
```
  ┃  △ Permission required
  ┃    ← Access external directory C:/Temp
  ┃  Patterns
  ┃  - C:/temp/*
  ┃   Allow once   Allow always   Reject    ctrl+f fullscreen  ⇆ select  enter confirm
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

# 欢迎页 LOGO 标记（█▀▀█ 块字符行）
_WELCOME_LOGO_RE = re.compile(r"█▀▀█\s+█▀▀█")

# 输入框：┃ 后文本（提示符后跟文本，允许前导空格）
_INPUT_RE = re.compile(r"^\s*┃\s?(.*)")

# 底部分隔线：╹▀▀▀▀ 或连续 ─ 或 ╹（允许前导空格，opencode 分隔线居中）
_SEPARATOR_RE = re.compile(r"^\s*(╹▀{10,}|─{10,}|╹─{10,})")

# 版本号：• OpenCode X.Y.Z 或行尾裸 X.Y.Z
_VERSION_RE = re.compile(r"(?:•\s*OpenCode\s+)?(\d+\.\d+\.\d+)")

# 上下文状态栏：N tokens (N%) 或 N.K tokens (N%)
_CONTEXT_PERCENT_RE = re.compile(r"\((\d+)%\)")

# 费用显示
_COST_DISPLAY_RE = re.compile(r"\$([\d.]+)\s*spent")

# Agent 信息行（非输入框，允许前导空格）
_AGENT_INFO_RE = re.compile(r"^\s*┃\s+Build\s*·")

# 模型名状态栏：Build · <model> · ...（不依赖 OpenCode，窄屏可能截断）
_MODEL_STATUS_RE = re.compile(r"Build\s*·\s*(.+?)(?:\s*·|$)")

# 工作目录：底部左侧路径
_CWD_DISPLAY_RE = re.compile(r"^([A-Za-z]:[/\\][^~]+|[~][/\\][^~]+)")

# 权限请求关键词
_PERMISSION_KEYWORDS = (
    "△ Permission required",
    "Allow once",
    "Allow always",
    "Reject",
    "ctrl+f fullscreen",
    "enter confirm",
)

# 进度条 + 工作中
_PROGRESS_RE = re.compile(r"[■⬝]+\s{2,}esc\s+interrupt")

# 思考标记
_THOUGHT_RE = re.compile(r"\+ Thought:\s*[\d.]+s")

# 工具执行标记
_TOOL_MARKS = (
    "$",
    "→ Read",
    "← Write",
    "← Edit",
    "# Wrote",
    "● Bash",
    "● Run",
)

# 无进度条时的明确工具行标记（不含裸 "$"：对话历史中的 shell 命令回显
# 也含 $，空闲对话会被误判 tool_running）
_TOOL_MARKS_NO_BARE = (
    "→ Read",
    "← Write",
    "← Edit",
    "# Wrote",
    "● Bash",
    "● Run",
)

# 对话内容标记
_CONVERSATION_MARKS = (
    "Context",
    "tokens",
    "% used",
    "▣",
    "Thought:",
    "Permission required",
    "esc interrupt",
)

# 输入框 placeholder 文本
_PLACEHOLDER = "Ask anything"

# 提问（question 工具）状态关键词
_ASK_KEYWORDS = (
    "↑↓ select",
    "enter submit",
    "esc dismiss",
    "Type your own answer",
)


def _detect_ai_status(lines: List[str]) -> str:
    """从消息区检测 AI 状态。

    Returns:
        idle / thinking / tool_running / awaiting_approval / awaiting_answer
    """
    joined = "\n".join(lines)

    for kw in _PERMISSION_KEYWORDS:
        if kw in joined:
            return "awaiting_approval"

    # question 工具提问：等待用户选择
    for kw in _ASK_KEYWORDS:
        if kw in joined:
            return "awaiting_answer"

    if _PROGRESS_RE.search(joined):
        # 有进度条 + esc interrupt = 工作中
        # 进一步区分 thinking vs tool_running
        if _THOUGHT_RE.search(joined):
            return "thinking"
        # 工具行关键词
        for mark in _TOOL_MARKS:
            if mark in joined:
                return "tool_running"
        return "thinking"

    # 无进度条帧（快速回复轮）：思考完成标记或明确工具行仍属工作中——
    # 否则快速轮全程误判 idle，TurnMonitor 的 _seen_busy 门控会挡住
    # 首轮完成通知（idle→idle 变化需见过 busy 才判完成）。
    # 注意：此处不用裸 "$"（对话历史中的 shell 命令回显也含 $，
    # 空闲对话会被误判 tool_running），仅用明确的工具行标记。
    if _THOUGHT_RE.search(joined):
        return "thinking"
    for mark in _TOOL_MARKS_NO_BARE:
        if mark in joined:
            return "tool_running"

    return "idle"


def _find_input_and_status(lines: List[str]) -> tuple:
    """定位输入框和状态栏。

    opencode TUI 布局：
    ```
    ┃ 输入框（┃ 后文本）
    ╹▀▀▀▀...  ← 底部分隔线
    cwd ... 1.18.21  ← 状态栏
    ```

    策略：
    1. 找到底部分隔线（╹▀/─ 行）
    2. 分隔线之上最近的 `┃` 行 = 输入框
    3. 分隔线之下 = 状态栏

    Returns:
        (input_text, bar_lines)
    """
    input_text = ""
    bar_lines: List[str] = []

    # 从下往上找第一条分隔线
    sep_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        if _SEPARATOR_RE.search(lines[i]):
            sep_idx = i
            break

    if sep_idx < 0:
        return input_text, bar_lines

    # 分隔线之下为状态栏（不 rstrip 保留空格）
    for j in range(sep_idx + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped:
            bar_lines.append(stripped)

    # 分隔线之上找最近的 `┃` 行（输入框）
    # 跳过 Agent 信息行（┃  Build · ...）与空 ┃ 行
    for i in range(sep_idx - 1, -1, -1):
        line = lines[i]
        if _AGENT_INFO_RE.match(line):
            continue
        m = _INPUT_RE.match(line)
        if m:
            text = m.group(1).strip()
            if text:
                # 过滤 placeholder（欢迎页）
                if _PLACEHOLDER in text:
                    input_text = ""
                else:
                    input_text = text
                break

    return input_text, bar_lines


def _parse_bar_context(bar_lines: List[str]) -> dict:
    """从状态栏行提取上下文信息。

    状态栏（分隔线之下）形如：
    ```
    C:/Users/alice/Desktop/opencodeparser  11.0K (1%)  ctrl+p commands    • OpenCode 1.18.21
    ```
    版本号也可能为行尾裸 `1.18.21`（欢迎页）。
    """
    merged = " ".join(line.strip() for line in bar_lines)
    result = {
        "context_tokens": 0,
        "context_percent": 0.0,
        "cost_display": "",
        "version_display": "",
        "cwd_display": "",
    }

    m = _VERSION_RE.search(merged)
    if m:
        result["version_display"] = m.group(1)

    m = _CONTEXT_PERCENT_RE.search(merged)
    if m:
        try:
            result["context_percent"] = float(m.group(1))
        except ValueError:
            pass

    m = _COST_DISPLAY_RE.search(merged)
    if m:
        result["cost_display"] = m.group(1)

    # cwd 从状态栏行中提取路径（盘符或 ~ 开头）
    if bar_lines:
        merged = " ".join(line.strip() for line in bar_lines)
        # 去掉版本号与快捷键提示后搜索路径
        cleaned = _VERSION_RE.sub("", merged)
        cleaned = cleaned.replace("ctrl+p", "").strip()
        # 提取路径：盘符（C:\）或 ~\ 开头，到空格或行尾
        pm = re.search(r"([A-Za-z]:[\\/][^\\/ ][^\s]*|[~][\\/][^\s]*)", cleaned)
        if pm:
            result["cwd_display"] = pm.group(1).strip()
        else:
            # 兜底：取第一行稳定版本
            result["cwd_display"] = cleaned.strip()[:60]

    return result


def _extract_right_panel(joined: str) -> dict:
    """从全屏文本中提取右侧栏（Context 面板）字段。

    右侧栏在宽屏（200x50）下可见，格式：
    ```
    Context
    N,N tokens      (或 N tokens)
    N% used
    $X.XX spent
    LSP / LSPs are disabled
    ```

    Returns:
        dict with context_tokens, context_percent, cost_display
    """
    result = {"context_tokens": 0, "context_percent": 0.0, "cost_display": ""}

    # tokens: "10,556 tokens" 或 "N tokens"
    m = re.search(r"([\d,]+)\s*tokens?", joined)
    if m:
        try:
            result["context_tokens"] = int(m.group(1).replace(",", ""))
        except ValueError:
            pass

    # percent: "N% used"（右侧栏格式）或 (N%)（状态栏格式，bar_context 已处理）
    m = re.search(r"(\d+)%\s*used", joined)
    if m:
        try:
            result["context_percent"] = float(m.group(1))
        except ValueError:
            pass

    # cost: "$X.XX spent"
    m = re.search(r"\$([\d.]+)\s*spent", joined)
    if m:
        result["cost_display"] = m.group(1)

    return result


def _extract_model_display(lines: List[str]) -> str:
    """从欢迎页或状态栏提取模型名。

    搜索 Build · <model> 行，不依赖 OpenCode 关键词（窄屏可能截断）。
    截断到 · 或 3+ 连续空格（分隔右侧栏）。
    """
    for line in lines:
        m = _MODEL_STATUS_RE.search(line.strip())
        if m:
            candidate = m.group(1).strip()
            # 截断到 · 或 3+ 空格（宽屏下右侧栏可能在同一行）
            import re as _re
            cm = _re.split(r"\s*·\s*|\s{3,}", candidate)
            if cm and cm[0]:
                candidate = cm[0].strip()
            if candidate and len(candidate) > 1:
                return candidate
    return ""


def _extract_version(lines: List[str]) -> str:
    """从已渲染行中提取版本号（兜底，行尾版本号可能被终端宽度裁剪）。"""
    for line in lines:
        m = _VERSION_RE.search(line)
        if m:
            return m.group(1)
    return ""


def extract_version_from_vt(vt_text: str) -> str:
    """直接从原始 VT 文本提取版本号。

    opencode 状态栏行尾的版本号（如 `1.18.21`）可能超出终端宽度，
    pyte 渲染后被裁剪，需在原始 VT 文本中直接搜索。
    """
    # 匹配 • OpenCode X.Y.Z 或行尾裸 X.Y.Z
    m = re.search(r"•\s*OpenCode\s+(\d+\.\d+\.\d+)", vt_text)
    if m:
        return m.group(1)
    m = re.search(r"(\d+\.\d+\.\d+)", vt_text)
    if m:
        return m.group(1)
    return ""


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

    joined = "\n".join(lines)

    # 界面类型：欢迎页 vs 对话中
    has_welcome = _WELCOME_LOGO_RE.search(joined) is not None
    has_conversation = any(
        kw in joined for kw in _CONVERSATION_MARKS
    )
    # 欢迎页 logo 优先；无 logo 或出现强对话标记时判对话中
    state.screen_type = "conversation" if (has_conversation and not has_welcome) else (
        "main" if has_welcome else "conversation"
    )

    # 输入框 + 状态栏
    input_text, bar_lines = _find_input_and_status(lines)
    state.input_text = input_text

    # 状态栏字段
    bar_info = _parse_bar_context(bar_lines)
    state.context_tokens = bar_info["context_tokens"]
    state.context_percent = bar_info["context_percent"]
    state.cost_display = bar_info["cost_display"]
    state.version_display = bar_info["version_display"] or _extract_version(lines)
    state.cwd_display = bar_info["cwd_display"]

    # 右侧栏（Context 面板）补充：宽屏下可见
    right = _extract_right_panel(joined)
    if right["context_tokens"]:
        state.context_tokens = right["context_tokens"]
    if right["context_percent"]:
        state.context_percent = right["context_percent"]
    if right["cost_display"]:
        state.cost_display = right["cost_display"]

    # 模型名
    state.model_display = _extract_model_display(lines)

    # AI 状态
    state.ai_status = _detect_ai_status(lines)

    _log.debug("parse_screen_lines: status=%s ctx=%s%% input=%r screen=%s model=%r",
               state.ai_status, state.context_percent, state.input_text,
               state.screen_type, state.model_display)
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
    state = parse_screen_lines(lines)
    # 版本号可能被终端宽度裁剪，从原始 VT 文本兜底提取
    if not state.version_display:
        version = extract_version_from_vt(vt_text)
        if version:
            state.version_display = version
    return state