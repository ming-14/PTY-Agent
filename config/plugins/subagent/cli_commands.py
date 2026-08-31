"""子代理 CLI 命令 — codebuddy / devin / opencode / claude exec

spawn 子代理会话。read/send/wait 复用系统命令，
由插件 decorate_response / 通知机制接管子代理会话。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from src.cli.base import Command, CommandContext
from src.client.presenter import print_response

_logger = logging.getLogger("pty-client")


class SubagentCommand(Command):
    """子代理管理命令基类（codebuddy / devin 各自子类化）"""

    name = ""                        # 命令名（子类覆盖）
    agent_id = ""                    # agents.py 中的 agent_id
    message_type = ""                # 消息类型
    help = ""
    use_common_args = False

    def _display_name(self) -> str:
        """显示名单一事实来源：agents.py 注册表"""
        from .agents import AGENTS
        spec = AGENTS.get(self.agent_id)
        return spec.display_name if spec else "SubAgent"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        from .agents import AGENTS
        spec = AGENTS.get(self.agent_id)

        sub = parser.add_subparsers(dest="subagent_subcmd", help="子代理子命令")

        # <cmd> exec sid -p "" [--cwd ""] [--model ""] [--program-path <path>] [--oneshot | --interactive]
        p_exec = sub.add_parser("exec", help="spawn 子代理会话")
        p_exec.add_argument("id", help="会话标识")
        p_exec.add_argument("-p", "--prompt", required=True, help="任务提示词")
        p_exec.add_argument("--cwd", default=None, help="工作目录")
        p_exec.add_argument("--model", default=None, help="模型名（如 hy3）")
        if spec is None or spec.has_program_path:
            p_exec.add_argument("--program-path", default=None,
                                help="子代理程序路径（不设置时按环境变量/PATH 查找）")
        mode = p_exec.add_mutually_exclusive_group()
        mode.add_argument("--oneshot", action="store_true", help="一次性模式（阻塞，完成后返回输出）")
        mode.add_argument("--interactive", action="store_true", help="交互模式（不阻塞，返回会话标识；默认）")

    def validate(self, args, parser: argparse.ArgumentParser) -> None:
        subcmd = getattr(args, "subagent_subcmd", None)
        if subcmd is None:
            parser.error(f"{self.name} 需要子命令: exec")

    def run(self, args, ctx: CommandContext) -> None:
        subcmd = args.subagent_subcmd
        if subcmd == "exec":
            self._run_exec(args, ctx)
        else:
            raise RuntimeError(f"未知子命令: {subcmd}")

    def _run_exec(self, args, ctx: CommandContext) -> None:
        """exec 子代理：interactive 走 ExecHandler 通用流程（含 check_ended_session）；
        oneshot 走插件 handle_message（_wait_and_return 轮询等待）。"""
        if bool(args.oneshot):
            return self._run_exec_oneshot(args, ctx)
        self._run_exec_interactive(args, ctx)

    def _run_exec_oneshot(self, args, ctx):
        """oneshot 走插件 handle_message（_wait_and_return 轮询等待）"""
        from .agents import AGENTS
        spec = AGENTS[self.agent_id]
        msg = {
            "type": spec.message_type,
            "id": args.id,
            "prompt": args.prompt,
            "oneshot": True,
        }
        if args.cwd:
            msg["cwd"] = args.cwd
        if args.model:
            msg["model"] = args.model
        if spec.has_program_path and getattr(args, "program_path", None):
            msg["programPath"] = args.program_path
        resp = ctx.client._send_recv(msg)
        if resp.get("type") == "error":
            print_response(resp)
            return
        render_exec_response(resp, self._display_name())

    def _run_exec_interactive(self, args, ctx):
        """interactive 走 ExecHandler 通用流程（含 check_ended_session 保护）"""
        import uuid
        import tempfile
        from .agents import AGENTS
        spec = AGENTS[self.agent_id]

        uid = None
        if spec.session_id_arg:
            if not spec.session_id_arg_oneshot_only:
                uid = str(uuid.uuid4())
        if uid is None and spec.export_arg:
            from .subagent_plugin import _ensure_export_dir
            export_dir = _ensure_export_dir()
            uid = os.path.join(export_dir, f"{uuid.uuid4()}.json")

        command = spec.build_command(
            prompt=args.prompt, model=args.model or None, uid=uid, oneshot=False,
        )
        from .subagent_plugin import _resolve_command
        try:
            program_path = getattr(args, "program_path", None) or ""
            command = _resolve_command(command, spec, program_path)
        except FileNotFoundError as e:
            print_response({"type": "error", "message": str(e)})
            return

        env = {"TERM": "xterm-256color"}
        data_dir = ""
        if spec.data_dir_env:
            try:
                data_dir = tempfile.mkdtemp(prefix="subagent-" + spec.agent_id + "-")
                env[spec.data_dir_env] = data_dir
            except Exception as e:
                _logger.warning("data_dir 隔离创建失败: %s", e)

        msg = {
            "type": "exec",
            "id": args.id,
            "command": command,
            "mode": "pty",
            "env": env,
            "timeout": 0.0,
            "subagent": {
                "agent": self.agent_id,
                "prompt": args.prompt,
                "oneshot": False,
                "uid": uid or "",
                "data_dir": data_dir,
            },
        }
        if args.cwd:
            msg["cwd"] = args.cwd
        if args.model:
            msg["model"] = args.model
        resp = ctx.client._send_recv(msg)
        if resp.get("type") == "error":
            print_response(resp)
            return
        display_name = self._display_name()
        sys.stdout.write(
            f"(SubAgent-{display_name} Message: 子代理 {display_name} 已启动，完成后会发通知)\n"
        )


def _build_agent_command(agent_id: str) -> type:
    """按 agents.py 注册表生成命令类（声明式接入：新增 agent 无需手写 Command）"""
    from .agents import AGENTS
    spec = AGENTS[agent_id]

    class _AgentCommand(SubagentCommand):
        name = spec.agent_id
        agent_id = spec.agent_id
        message_type = spec.message_type
        help = f"子代理管理（spawn {spec.display_name} 子进程；read/send 走系统命令）"

    _AgentCommand.__name__ = f"{spec.display_name}Command"
    _AgentCommand.__qualname__ = _AgentCommand.__name__
    return _AgentCommand


def all_agent_commands() -> list:
    """按 agents.py 注册表顺序生成全部 agent 的 CLI 命令类"""
    from .agents import AGENTS
    return [_build_agent_command(aid) for aid in AGENTS]


commands = all_agent_commands()


def render_exec_response(resp: dict, display_name: str = "SubAgent") -> None:
    """渲染 oneshot exec 响应（_wait_and_return 的字段结构）"""
    import sys
    import shutil

    if resp.get("type") == "error":
        print_response(resp)
        return

    sid = resp.get("sessionId", "")
    output = resp.get("outputStream", "")
    exit_code = resp.get("exitCode")
    duration_ms = resp.get("duration_ms", 0)
    cols = shutil.get_terminal_size((80, 24)).columns

    def _sep(label=""):
        if label:
            inner = f" {label} "
            dashes = cols - sum(2 if ord(c) > 127 else 1 for c in inner)
            dashes = max(dashes, 0)
            left = dashes // 2
            return "─" * left + inner + "─" * (dashes - left)
        return "─" * cols

    sys.stdout.write(_sep("oneshot") + "\n")
    if output:
        sys.stdout.write(output.rstrip() + "\n")
    sys.stdout.write(_sep() + "\n")

    tags = []
    if exit_code is not None:
        tags.append(f"exit {exit_code}")
    if duration_ms:
        tags.append(f"{duration_ms / 1000:.1f}s")
    tag_str = " · ".join(tags)
    sys.stdout.write(f"[{resp.get('commandType','exec')} · {tag_str}]  {sid}\n")


def _render_list_with_ai_status(resp: dict) -> str | None:
    """渲染 list 响应：子代理会话 STATE 列显示 subagent_<ai_status>

    仅当响应含 subagent_status 字段的会话时接管渲染（返回表格文本）；
    否则返回 None 走默认渲染。表格布局与 presenter 默认一致。
    """
    sessions = resp.get("sessions") or []
    if not any(s.get("subagent_status") for s in sessions):
        return None

    def _trunc(text, n):
        text = text or ""
        return text if len(text) <= n else text[: n - 3] + "..."

    def _table(headers, rows):
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

    def _fmt_duration(s):
        """会话运行时长：运行中=从 startTime 到现在；ended=startTime→endTime"""
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

    headers = ("ID", "COMMAND", "TIME", "STATE")
    rows = []
    for s in sessions:
        if s.get("subagent_status"):
            state = "subagent_" + s["subagent_status"]
        else:
            state = "running" if s.get("running") else "ended"
        rows.append((
            s.get("id", ""),
            _trunc(s.get("rawStartCommand") or s.get("command") or "", 24),
            _fmt_duration(s),
            state,
        ))
    hint = resp.get("hint", "")
    text = _table(headers, rows)
    if hint:
        text += "\n" + hint
    return text + "\n"