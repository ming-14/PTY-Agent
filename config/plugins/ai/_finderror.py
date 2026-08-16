import argparse
import base64
import os
import sys

from common import (
    SCRIPT_DIR, DEFAULT_CONFIG,
    check_config, run_aichat,
)

DEFAULT_SKILL = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "SKILL.md")
)

PROMPT = (
    "===================== 系统指令 ====================="
    "用户想使用这个命令行工具，但是用法可能出现错误，请你根据SKILL.md文档和用户输入的命令，推断出用户意图，并给出正确的命令执行方法\\n"
    "**回复格式**（你只要填空就好）：“命令有误。你似乎想____。根据文档：___（原文）___。错误点在于____。正确的命令应该是____。请再次阅读 SKILL.md 文档”\\n"
    "## 注意点：\\n\\n"
    "（重要）**不要检查执行入口的问题不要检查执行入口的问题不要检查执行入口的问题，能问到你说明执行入口一定是对的能问到你说明执行入口一定是对的能问到你说明执行入口一定是对的能问到你说明执行入口一定是对的，给出的正确命令示例的执行入口应该采用用户的方式**\\n"
    "如果你无法找出哪里错误，请不要纠结，只回复一句话就好：“请重新阅读SKILL.md，并且换一种方式执行”\\n"
    "\\n"
    "用户原始命令："
)


def cmd_run(args: argparse.Namespace) -> int:
    if not os.path.exists(args.skill):
        sys.stderr.write(f"Cannot find SKILL.md: {args.skill}\n")
        return 1

    err = check_config(args.config)
    if err:
        sys.stderr.write(err + "\n")
        return 1

    user_command = args.user_command
    if user_command.startswith("base64:"):
        user_command = base64.b64decode(user_command[len("base64:"):]).decode("utf-8")
    text = PROMPT + user_command

    return run_aichat(
        ["-f", args.skill, text],
        config=args.config,
        timeout=args.timeout,
        no_think=args.no_think,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find errors in user commands using aichat")
    parser.add_argument("--user-command", required=True, help="Raw user command to analyze")
    parser.add_argument("--skill", default=DEFAULT_SKILL, help=f"Path to SKILL.md (default: {DEFAULT_SKILL})")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"Path to config.yaml (default: {DEFAULT_CONFIG})")
    parser.add_argument("--timeout", type=int, default=40, help="Timeout in seconds (default: 40)")
    parser.add_argument("--no-think", action="store_true", help="Strip <think> block from output")
    args = parser.parse_args(argv)
    return args


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "config":
        from config_manager import run_config
        return run_config(argv[1:])

    args = parse_args(argv)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
