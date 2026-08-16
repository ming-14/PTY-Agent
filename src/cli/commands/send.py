"""send 命令：向运行中的会话发送输入"""

import argparse

from ..base import Command, CommandContext
from ..common_args import add_output_args, add_session_io_args, warn_idle_without_idle_timeout


class SendCommand(Command):
    """send 命令"""

    name = "send"
    help = "向运行中的会话发送输入"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("id", help="会话标识")
        parser.add_argument(
            "-i", "--input", required=True, help="要发送的输入文本（最长 65536 字符）"
        )
        add_session_io_args(parser)
        parser.add_argument(
            "--full",
            action="store_true",
            default=False,
            help="返回全部累积输出而非仅新输出",
        )
        parser.add_argument(
            "--json-escaping",
            "-j",
            action="store_true",
            default=False,
            help="启用 JSON + 控制字符转义解码（\\n→换行；{ctrl+a}、{enter}、{up}、{f1} 等生成对应控制字符/VT 序列）；默认 raw 模式原样发送",
        )
        parser.add_argument(
            "--send-eol",
            "-e",
            default=None,
            choices=["lf", "crlf", "cr", "none"],
            help="末尾追加的行尾符（默认 cr=\\r，模拟终端 Enter 键；lf=\\n；crlf=\\r\\n；none=不追加）",
        )
        add_output_args(parser)

    def validate(self, args, parser: argparse.ArgumentParser) -> None:
        warn_idle_without_idle_timeout(args)

    def run(self, args, ctx: CommandContext) -> None:
        ctx.client.cmd_send(
            session_id=args.id,
            input_text=args.input,
            trigger=args.trigger,
            newline=args.newline,
            fresh=True,
            timeout=args.timeout,
            encoding=args.encoding,
            full=args.full,
            keep_ansi=args.keep_ansi,
            idle_timeout=args.idle_timeout,
            idle_after_first_output=args.idle_after_first_output,
            json_escaping=args.json_escaping,
            send_eol=args.send_eol,
            output_path=args.output_path,
            response_format=args.response_format,
            svg_compression_level=args.svg_compression_level,
            snapshot_diff=args.snapshot_diff,
        )
