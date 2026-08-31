"""通用状态检查插件 — 纯启发式终端状态检测

插件仅保留两个钩子：
1. 响应装饰（decorate_response）：装饰 list 响应，给普通 PTY 会话的 status 追加
   HEUR 状态标记（格式：running - HEUR:Repl）。
2. CLI 渲染（render_response）：list 响应打印前接管 STATE 列渲染，直接显示
   status 字段（autoMount 声明，list 命令无需 --plugin 激活）。

数据源:
- 屏幕快照:  session.get_snapshot()（纯文本）
- 光标位置:  session.cursor_position()（0-based 列/行）
- 备用屏幕:  session.is_alt_screen()（终端层跟踪）
- 前台进程:  会话进程树进程名（psutil 解析，缺依赖时该级跳过）

未启用级别：1（进程退出）、3（REPL 提示符）、6（自定义 shell 正则）。
"""

import re

from src.plugins.base import Plugin

# 前台进程 shell 名单（优先级 5）
SHELL_NAMES = ("bash", "zsh", "sh", "fish", "dash", "tcsh", "csh", "pwsh", "powershell")

# 优先级 4：Shell 提示符正则（8 种，仅最后一行）
SHELL_PROMPTS = (
    r"^[^$#>%@\s]*\$\s?$", r"^[^$#>%@\s]*#\s?$",
    r"^[^$#>%@\s]*>\s?$", r"^[^$#>%@\s]*%\s?$",
    r"\$\s?$", r"#\s?$", r">\s?$", r"%\s?$",
)

# 优先级 7：编辑器模式指示词（10 种，仅最后一行）
EDITOR_INDICATORS = (
    "-- insert --", "-- normal --", "-- visual --", "-- replace --",
    "-- 插入 --", "-- 普通 --", "-- 可视 --", "-- 替换 --",
    "gnu nano", "^g get help",
)

# 优先级 8：分页器模式指示词（5 种，仅最后一行）
PAGER_INDICATORS = ("(end)", "manual page", "--more--", "press q to quit", "space for next")

# 优先级 9：Agent 权限提示关键词组合（3 种，全文同时匹配）
AGENT_PERMISSION_PATTERNS = (
    ("do you want", "yes"),
    ("needs permission", "allow"),
    ("esc to cancel", "allow"),
)

# 优先级 10：密码提示指示词（8 种，仅最后一行）
# 只看最后一行：密码提示是当前等待输入的提示符，输入后该行滚出，
# 状态应变更为后续提示；若看最近 3 行，密码行残留会长期压制 Confirm/Error
PASSWORD_PATTERNS = (
    "password:", "password for", "passphrase:", "passphrase for",
    "enter password", "enter passphrase", "[sudo]", "secret:",
)

# 优先级 11：确认提示指示词（12 种，仅最后一行）
CONFIRM_INDICATORS = (
    "[y/n]", "[yes/no]", "continue?", "are you sure", "proceed?",
    "(y/n)", "[Y/n]", "[y/N]", "confirm?", "accept?",
    "do you want", "shall i", "would you like",
)

# 优先级 12：错误指示词（17 种，最近 3 行）
ERROR_INDICATORS = (
    "error:", "failed:", "fatal:", "exception:", "traceback",
    "indexerror", "keyerror", "nameerror", "typeerror", "valueerror",
    "syntaxerror", "runtimeerror", "panic:", "abort:",
    "segmentation fault", "core dumped", "command not found",
)


def _is_command_output_line(line: str) -> bool:
    """判断一行是否为命令输出行（以 $ / # / > / % 开头）"""
    line = line.strip()
    if not line:
        return False
    return line.startswith(("$ ", "# ", "> ", "% "))


def _process_name(pid: int):
    """解析进程名（psutil 优先；不可用时返回 None，对应优先级跳过）"""
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return None


# ── CLI 侧渲染辅助（render_response 用） ──────────────────

def _trunc(text, n: int) -> str:
    """截断文本到 n 字符，超长补 ..."""
    text = text or ""
    return text if len(text) <= n else text[: n - 3] + "..."


def _fmt_duration(s: dict) -> str:
    """会话运行时长"""
    import time
    start = s.get("startTime")
    if not start:
        return ""
    end = s.get("endTime") if not s.get("running") else time.time()
    try:
        secs = max(0, float(end) - float(start))
    except (TypeError, ValueError):
        return ""
    if secs < 60:
        return f"{secs:.0f}s"
    if secs < 3600:
        return f"{secs / 60:.0f}m"
    return f"{secs / 3600:.1f}h"


def _render_table(headers, rows) -> str:
    """渲染对齐表格（两空格分隔）"""
    widths = [len(h) for h in headers]
    for row in rows:
        for i in range(min(len(headers), len(row))):
            widths[i] = max(widths[i], len(str(row[i])))
    lines = [
        "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip(),
        "  ".join("-" * widths[i] for i in range(len(headers))),
    ]
    for row in rows:
        lines.append(
            "  ".join(
                str(row[i]).ljust(widths[i])
                for i in range(min(len(headers), len(row)))
            ).rstrip()
        )
    return "\n".join(lines)


def _render_list_with_state_check(resp: dict) -> str | None:
    """渲染 list 响应：STATE 列优先显示 status 字段（含 HEUR 标记）

    仅当响应含 status 字段的会话时接管渲染（返回表格文本）；
    否则返回 None 走默认渲染。
    """
    sessions = resp.get("sessions") or []
    if not any(s.get("status") for s in sessions):
        return None
    headers = ("ID", "COMMAND", "TIME", "STATE")
    rows = []
    for s in sessions:
        state = s.get("status") or ("running" if s.get("running") else "ended")
        rows.append(
            (
                s.get("id", ""),
                _trunc(s.get("rawStartCommand") or s.get("command") or "", 24),
                _fmt_duration(s),
                state,
            )
        )
    return _render_table(headers, rows)


class StateCheckPlugin(Plugin):
    """状态检查（纯启发式）：装饰 list 响应 + CLI 显示 HEUR 标记"""

    # ── 响应装饰：list ─────────────────────────────────────

    def decorate_response(self, ctx, resp: dict) -> dict | None:
        """装饰 list 响应，给普通 PTY 会话的 status 追加 HEUR 标记"""
        ctype = resp.get("commandType")
        if ctype == "list":
            return self._decorate_list(ctx, resp)
        return None

    def _decorate_list(self, ctx, resp: dict) -> dict | None:
        """list：给普通 PTY 会话补 status 标记（格式：running - HEUR:状态）"""
        sessions = resp.get("sessions")
        if not sessions:
            return None
        manager = ctx.manager
        if manager is None:
            return None
        modified = False
        for s in sessions:
            if not s.get("running"):
                continue
            sid = s.get("id")
            if not sid:
                continue
            session = manager.get_session(sid)
            if session is None:
                continue
            # 条件 1：普通 exec 创建（来源标记含 "normal"）
            if not session.has_common_mark("normal"):
                continue
            # 条件 2：PTY 模式
            if getattr(session, "mode", "") != "pty":
                continue
            state, _ = self._detect_session(session)
            if state is None:
                continue
            existing = s.get("status") or ("running" if s.get("running") else "ended")
            s["status"] = "{} - HEUR:{}".format(existing, state)
            modified = True
        return resp if modified else None

    def _detect_session(self, session):
        """对会话对象运行状态检测，返回 (state, reason)"""
        try:
            text = session.get_snapshot() or ""
            cursor = session.cursor_position()
            cursor_x = cursor[0] if cursor else None
            alt = session.is_alt_screen()
        except Exception:
            text, cursor_x, alt = "", None, False
        process = None
        try:
            pids = session.get_pty_process_list() or []
        except Exception:
            pids = []
        for pid in pids:
            name = _process_name(pid)
            if name and any(s in name.lower() for s in SHELL_NAMES):
                process = name
                break
        return self._detect(text, cursor_x, alt, process)

    # ── CLI 渲染：list 响应打印前 ──────────────────────────

    def render_response(self, ctx, resp: dict) -> str | None:
        """CLI 侧渲染：list 响应 STATE 列显示 status（含 HEUR 标记）"""
        ctype = resp.get("commandType")
        if getattr(ctx, "command", "") == "list" and ctype == "list":
            return _render_list_with_state_check(resp)
        return None

    # ── 检测逻辑 ───────────────────────────────────────────

    def _detect(self, screen_text: str, cursor_x, is_alt_screen: bool, process_name):
        """按优先级表顺序检测，匹配即返回 (状态, 描述)

        未启用级别: 1（进程退出）、3（REPL 提示符）、6（自定义 shell 正则）。
        """
        lines = screen_text.split("\n")
        non_empty = [ln.rstrip() for ln in lines if ln.strip()]
        if not non_empty:
            return None, "empty screen"
        tail_last = non_empty[-1]
        tail_last_lower = tail_last.lower()
        recent = "\n".join(ln.lower() for ln in non_empty[-3:])
        whole_lower = screen_text.lower()

        # 2. 备用屏幕激活 → Alt-Screen
        if is_alt_screen:
            return "Alt-Screen", "alt screen active"

        # 防误匹配：命令输出行不作为提示符判定
        is_prompt_line = not _is_command_output_line(tail_last)

        # 4. 最后一行 Shell 提示符且光标不在行首 → WaitingForInput
        if is_prompt_line and cursor_x != 0:
            for pattern in SHELL_PROMPTS:
                if re.search(pattern, tail_last):
                    return "WaitingForInput", "shell prompt"

        # 5. 前台进程是 shell → WaitingForInput
        if process_name:
            return "WaitingForInput", f"shell process ({process_name})"

        # 7. 最后一行编辑器模式指示词 → Editor
        if any(ind in tail_last_lower for ind in EDITOR_INDICATORS):
            return "Editor", "editor mode indicator"

        # 8. 最后一行分页器指示词 → Pager
        if any(ind in tail_last_lower for ind in PAGER_INDICATORS):
            return "Pager", "pager indicator"

        # 9. 全文同时命中权限关键词组合 → Confirm
        if any(all(kw in whole_lower for kw in pair) for pair in AGENT_PERMISSION_PATTERNS):
            return "Confirm", "agent permission prompt"

        # 10. 最后一行密码提示 → Password
        if any(p in tail_last_lower for p in PASSWORD_PATTERNS):
            return "Password", "password prompt"

        # 11. 最后一行确认提示 → Confirm
        if any(i in tail_last_lower for i in CONFIRM_INDICATORS):
            return "Confirm", "confirm prompt"

        # 12. 最近 3 行错误指示词 → Error（优先于 Running：错误输出以换行结尾时
        #     光标常回到行首 col 0，若 Running 在前则 Error 永不命中）
        if any(i in recent for i in ERROR_INDICATORS):
            return "Error", "error indicator"

        # 13. 光标在行首 → Running（命令执行中）
        if cursor_x == 0:
            return "Running", "cursor at column 0"

        return None, "no match"


plugin = StateCheckPlugin