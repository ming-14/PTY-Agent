"""read 命令：读取会话终端输出"""

import argparse

from ..base import Command, CommandContext
from ..common_args import abort_error, add_output_args, add_session_io_args


class ReadCommand(Command):
    """read 命令"""

    name = "read"
    help = "读取会话终端输出"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("id", help="会话标识")
        add_session_io_args(parser)
        parser.add_argument(
            "--lines", "-l", default=None, help="行数过滤: N=最后N行, start:end=范围"
        )
        parser.add_argument("--grep", "-g", default=None, help="正则匹配过滤行")
        parser.add_argument(
            "--offset", type=int, default=None, help="增量读取：从指定字节偏移开始"
        )
        parser.add_argument(
            "--full",
            action="store_true",
            default=False,
            help="返回全部累积输出而非仅新输出",
        )
        add_output_args(parser)
        parser.add_argument(
            "--column",
            type=int,
            default=None,
            metavar="N",
            help="输出第 N 列（1-based 字符位，PTY 快照行与子进程输出行均适用）",
        )

    def validate(self, args, parser: argparse.ArgumentParser) -> None:
        if args.offset and args.full:
            abort_error("--offset cannot be used with --full")

    def run(self, args, ctx: CommandContext) -> None:
        ctx.client.cmd_read(
            session_id=args.id,
            trigger=args.trigger,
            newline=args.newline,
            timeout=args.timeout,
            idle_timeout=args.idle_timeout,
            idle_after_first_output=args.idle_after_first_output,
            lines=args.lines,
            grep=args.grep,
            offset=args.offset,
            encoding=args.encoding,
            full=args.full,
            keep_ansi=args.keep_ansi,
            output_path=args.output_path,
            response_format=args.response_format,
            svg_compression_level=args.svg_compression_level,
            snapshot_diff=args.snapshot_diff,
            column=args.column,
        )
