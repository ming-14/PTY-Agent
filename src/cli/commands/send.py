"""send / advsend 命令：向运行中的会话发送输入

send 原样发送；advsend 与其参数一致，但恒启用心 JSON + 控制字符转义解码
（等价于旧 send -j 模式）。
"""

import argparse

from ..base import Command, CommandContext
from ..common_args import add_output_args, add_session_io_args, warn_idle_without_idle_timeout


class _SendInputCommand(Command):
    """send 与 advsend 的共用实现，差异仅是否对输入做转义解码。"""

    # 命令是否恒启用 JSON + 控制字符转义解码（advsend 覆写为 True）
    json_escaping = False

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
            "--lines",
            "-l",
            default=None,
            help="行数过滤: N=最后N行, start:end=范围",
        )
        parser.add_argument(
            "--send-eol",
            "-e",
            default=None,
            choices=["lf", "crlf", "cr", "none"],
            help="末尾追加的行尾符（默认按会话模式：pty=\\r 模拟终端 Enter；"
                 "subprocess=\\n；可选 cr=\\r；lf=\\n；crlf=\\r\\n；none=不追加）",
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
            lines=args.lines,
            idle_timeout=args.idle_timeout,
            idle_after_first_output=args.idle_after_first_output,
            json_escaping=self.json_escaping,
            send_eol=args.send_eol,
            output_path=args.output_path,
            response_format=args.response_format,
            svg_compression_level=args.svg_compression_level,
            snapshot_diff=args.snapshot_diff,
            notify=args.notify,
        )


class SendCommand(_SendInputCommand):
    """send 命令：原样发送，不做转义"""

    name = "send"
    help = "向运行中的会话发送输入"
    json_escaping = False


class AdvSendCommand(_SendInputCommand):
    """advsend 命令：与 send 一致，但恒启用 JSON + 控制字符转义解码"""

    name = "advsend"
    help = "向运行中的会话发送输入（JSON + 控制字符转义解码）"
    json_escaping = True