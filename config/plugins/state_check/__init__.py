"""通用状态检查插件 — 纯启发式终端状态检测

插件仅提供两个钩子：
1. 返回钩子（inspect_state）：命令返回时（exec/send/read/mouse 响应构造）触发一次，
   检查当前终端状态，检测结果作为 terminalState 附加到返回信息。
2. 命令钩子（handle_command）：plugin cmd <id> state_check status 查询当前状态。

无轮询、无事件订阅、不干预命令执行。按优先级顺序检查屏幕快照/光标位置/
备用屏幕/前台进程，匹配即返回状态（Editor/Repl/WaitingForInput/Pager/
Confirm/Password/Running/Error）。

数据源:
- 屏幕快照:  ctx.session.get_snapshot()（纯文本）
- 光标位置:  ctx.session.cursor_position()（0-based 列/行）
- 备用屏幕:  ctx.session.is_alt_screen()（终端层跟踪）
- 前台进程:  会话进程树进程名（psutil 解析，缺依赖时该级跳过）

未启用级别：1（进程退出）、6（自定义 shell 正则）。
"""

import logging
import re

from src.plugins.base import Plugin

_logger = logging.getLogger("pty-plugins")

# 前台进程 shell 名单（优先级 5）
SHELL_NAMES = ("bash", "zsh", "sh", "fish", "dash", "tcsh", "csh", "pwsh", "powershell")

# 优先级 3：REPL 提示符正则（19 种，仅最后一行）
REPL_PROMPTS = (
    r"^>>>\s?$", r"^\.\.\.\s?$", r"^In \[\d+\]:\s?$", r"^Out\[\d+\]:\s?$",
    r"^\(Pdb\)\s?$", r"^ipdb>\s?$", r"^irb>\s?$", r"^pry>\s?$",
    r"^mysql>\s?$", r"^mariadb>\s?$", r"^psql>\s?$", r"^postgres=#\s?$",
    r"^postgres=>\s?$", r"^sqlite>\s?$", r"^lua>\s?$", r"^node>\s?$",
    r"^julia>\s?$", r"^sage>\s?$", r"^ghci>\s?$",
)

# 优先级 4：Shell 提示符正则（8 种，仅最后一行）
SHELL_PROMPTS = (
    r"^[^$#>%@\s]*\$\s?$", r"^[^$#>%@\s]*#\s?$",
    r"^[^$#>%@\s]*>\s?$", r"^[^$#>%@\s]*%\s?$",
    r"\$\s?$", r"#\s?$", r">\s?$", r"%\s?$",
)

# 优先级 7：编辑器模式指示词（6 种，仅最后一行）
EDITOR_INDICATORS = (
    "-- insert --", "-- normal --", "-- visual --", "-- replace --",
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

# 优先级 13：错误指示词（17 种，最近 3 行）
ERROR_INDICATORS = (
    "error:", "failed:", "fatal:", "exception:", "traceback",
    "indexerror", "keyerror", "nameerror", "typeerror", "valueerror",
    "syntaxerror", "runtimeerror", "panic:", "abort:",
    "segmentation fault", "core dumped", "command not found",
)


def _is_command_output_line(line: str) -> bool:
    """判断一行是否为命令输出行（以 $ / # / > / % 开头）

    防误匹配：命令输出（如文件内容、帮助文本）里可能恰好以提示符字样结尾，
    这类行不作为提示符判定（REPL/Shell 提示符检测的先行条件）。
    """
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


class StateCheckPlugin(Plugin):
    """通用状态检查（纯启发式）：命令返回时检测终端状态并附加到返回信息

    元信息（id/kind/hooks/权限）见同目录 plugin.json；无事件/定时触发。
    """

    # ── 返回钩子：命令返回时触发一次 ───────────────────────

    def inspect_state(self, ctx):
        """命令返回时检测终端状态，随响应返回 terminalState"""
        try:
            text = ctx.session.get_snapshot() or ""
            cursor = ctx.session.cursor_position()
            cursor_x = cursor[0] if cursor else None
            alt = ctx.session.is_alt_screen()
        except Exception:
            text, cursor_x, alt = "", None, False
        process = self._foreground_process_name(ctx)
        state, reason = self._detect(text, cursor_x, alt, process)
        return {
            "state": state,
            "reason": reason,
            "altScreen": alt,
        }

    # ── 命令钩子：外部查询当前状态 ─────────────────────────

    def handle_command(self, ctx, msg):
        if msg.get("command") == "status":
            return self.inspect_state(ctx)
        return None

    # ── 内部实现 ───────────────────────────────────────────

    def _foreground_process_name(self, ctx):
        """返回进程树中首个 shell 进程名；无 shell 进程或解析失败返回 None"""
        try:
            pids = ctx.session.get_pty_process_list() or []
        except Exception:
            return None
        for pid in pids:
            name = _process_name(pid)
            if name and any(s in name.lower() for s in SHELL_NAMES):
                return name
        return None

    def _detect(self, screen_text: str, cursor_x, is_alt_screen: bool, process_name):
        """按优先级表顺序检测，匹配即返回 (状态, 描述)

        输入 screen_text 为纯文本快照；未启用级别: 1（进程退出）、6（自定义 shell 正则）。
        """
        lines = screen_text.split("\n")
        non_empty = [ln.rstrip() for ln in lines if ln.strip()]
        if not non_empty:
            return None, "empty screen"
        tail_last = non_empty[-1]
        tail_last_lower = tail_last.lower()
        recent = "\n".join(ln.lower() for ln in non_empty[-3:])
        whole_lower = screen_text.lower()

        # 2. 备用屏幕激活 → Editor（vim/htop/less 等 TUI）
        if is_alt_screen:
            return "Editor", "alt screen active"

        # 防误匹配：命令输出行（$ / # / > / % 开头）不作为提示符判定
        is_prompt_line = not _is_command_output_line(tail_last)

        # 3. 最后一行 REPL 提示符且光标不在行首 → Repl
        if is_prompt_line and cursor_x != 0:
            for pattern in REPL_PROMPTS:
                if re.search(pattern, tail_last):
                    return "Repl", "repl prompt"

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

        # 12. 光标在行首 → Running（命令执行中）
        if cursor_x == 0:
            return "Running", "cursor at column 0"

        # 13. 最近 3 行错误指示词 → Error
        if any(i in recent for i in ERROR_INDICATORS):
            return "Error", "error indicator"

        return None, "no match"


plugin = StateCheckPlugin