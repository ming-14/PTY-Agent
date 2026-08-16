"""命令注册表

职责：
- register() 注册命令（注册期冲突检测：name 非空且唯一）
- build_parser() 构建顶层解析器与全部子命令解析器（选项冲突由 argparse 在构建期暴露）
- dispatch() 按 args.subcmd 定位命令，先 validate 再 run
"""

import argparse
import re
import sys
from typing import Dict

from .base import Command, CommandContext
from .common_args import add_common_args


class _HintParser(argparse.ArgumentParser):
    """带友好提示的参数解析器：识别误把程序路径当命令输入的场景"""

    def error(self, message):
        if "invalid choice" in message:
            m = re.search(r"'([^']+)'", message)
            if m:
                bad = m.group(1)
                if any(c in bad for c in ("/", "\\", ".")):
                    print(
                        "\n提示: 如需启动程序，请使用 exec 命令:\n"
                        f'  pty-agent exec my-session -c "{bad}"\n'
                        "示例:\n"
                        f'  pty-agent exec build -c "{bad} --help" -t "error"\n',
                        file=sys.stderr,
                    )
        super().error(message)


class CommandRegistry:
    """命令注册表：注册 → 构建解析器 → 派发"""

    def __init__(self) -> None:
        self._commands: Dict[str, Command] = {}

    def register(self, cmd: Command) -> None:
        """注册命令；name 非空且唯一（注册期冲突检测）"""
        if not cmd.name:
            raise ValueError("命令 name 不能为空")
        if cmd.name in self._commands:
            raise ValueError(
                f"命令名冲突: '{cmd.name}' 已注册为 {type(self._commands[cmd.name]).__name__}"
            )
        self._commands[cmd.name] = cmd

    def get(self, name: str) -> Command:
        return self._commands[name]

    def build_parser(
        self, *, prog: str, description: str, epilog: str
    ) -> argparse.ArgumentParser:
        """构建顶层解析器与全部子命令解析器

        构建即全量冲突扫描：重复子命令名 / 子命令内重复选项字符串均由 argparse 暴露。
        """
        parser = _HintParser(
            prog=prog,
            description=description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=epilog,
        )
        parser.add_argument(
            "--show-config",
            nargs="?",
            const="",
            default=None,
            metavar="KEY",
            help="查看配置值（不指定 KEY 则显示全部）",
        )
        sub = parser.add_subparsers(dest="subcmd", help="可用命令")
        for name, cmd in self._commands.items():
            kwargs = {"help": cmd.help, "description": cmd.description}
            if cmd.formatter_class:
                kwargs["formatter_class"] = cmd.formatter_class
            p = sub.add_parser(name, **kwargs)
            if cmd.use_common_args:
                add_common_args(p)
            cmd.add_arguments(p)
        return parser

    def dispatch(self, args, ctx: CommandContext) -> None:
        """按 args.subcmd 派发：先 validate（冲突检测）再 run"""
        cmd = self._commands[args.subcmd]
        cmd.validate(args, ctx.parser)
        cmd.run(args, ctx)
