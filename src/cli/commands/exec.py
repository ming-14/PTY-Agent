"""exec 命令：启动或附加到会话"""

import argparse

from ..base import Command, CommandContext
from ..common_args import add_output_args, add_session_io_args, warn_idle_without_idle_timeout


class _EnvAppendAction(argparse.Action):
    """--env 累积动作：多次 --env KEY=VALUE 与单次多个 KEY=VALUE 都生效"""

    def __call__(self, parser, namespace, values, option_string=None):
        items = getattr(namespace, self.dest, None)
        if items is None:
            items = []
        items.extend(values)
        setattr(namespace, self.dest, items)


class ExecCommand(Command):
    """exec 命令"""

    name = "exec"
    help = "启动或附加到会话"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("id", help="会话标识")
        parser.add_argument(
            "--command",
            "-c",
            default=None,
            help="要执行的命令字符串（自动拆分为参数列表执行）",
        )
        parser.add_argument(
            "--force-pty-mode",
            action="store_true",
            default=False,
            help="强制模式：忽略 shell 操作符检测，原样拆分执行",
        )
        add_session_io_args(parser)
        parser.add_argument(
            "--full",
            action="store_true",
            default=False,
            help="返回全部累积输出而非仅新输出",
        )
        parser.add_argument(
            "--lines",
            "-l",
            default=None,
            help="行数过滤: N=最后N行, start:end=范围",
        )
        add_output_args(parser)
        parser.add_argument(
            "--cwd", default=None, help="指定子进程工作目录（默认为守护进程当前目录）"
        )
        parser.add_argument(
            "--env",
            nargs="*",
            action=_EnvAppendAction,
            default=None,
            help="子进程环境变量，格式 KEY=VALUE，可指定多个（支持多次 --env 与单次多个）",
        )
        parser.add_argument(
            "--subprocess",
            action="store_true",
            default=False,
            help="子进程模式：用 Popen 直接捕获 stdout/stderr（非 PTY），"
            "无终端回显、无快照、无 resize；增量输出+stderr 分离，支持写 stdin",
        )
        parser.add_argument(
            "--shell",
            default=None,
            metavar="SHELL",
            help="用指定 shell 包装执行命令（如 bash/cmd/pwsh；命令内的 shell 操作符 "
            "| & > < && || ; 由该 shell 解析）。不指定时用 set-default shell（默认无包装）",
        )
        parser.add_argument(
            "--size", default=None, metavar="WxH", help="终端尺寸（如 120x40，默认 80x24）"
        )
        parser.add_argument(
            "--plugin",
            action="append",
            default=None,
            dest="plugins",
            help="挂载插件到会话（可多次指定；按插件形态自动分流：daemon 形态在 daemon 挂载，"
            "CLI 形态记录到会话，后续 read/send/mouse 自动回调）",
        )

    def validate(self, args, parser: argparse.ArgumentParser) -> None:
        if not args.command:
            parser.error("'exec' 命令需要 --command/-c 参数")
        warn_idle_without_idle_timeout(args)

    def run(self, args, ctx: CommandContext) -> None:
        ctx.client.cmd_exec(
            session_id=args.id,
            command=args.command,
            trigger=args.trigger,
            newline=args.newline,
            fresh=True,
            timeout=args.timeout,
            encoding=args.encoding,
            full=args.full,
            keep_ansi=args.keep_ansi,
            lines=args.lines,
            idle_timeout=args.idle_timeout,
            idle_after_first_output=args.idle_after_first_output,
            force=args.force_pty_mode,
            cwd=args.cwd,
            env=args.env,
            output_path=args.output_path,
            response_format=args.response_format,
            svg_compression_level=args.svg_compression_level,
            snapshot_diff=args.snapshot_diff,
            size=args.size,
            plugins=args.plugins,
            mode="subprocess" if args.subprocess else "pty",
            shell=args.shell,
            notify=args.notify,
        )
