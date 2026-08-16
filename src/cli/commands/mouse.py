"""mouse 命令：发送鼠标动作到 PTY 会话"""

import argparse

from ..base import Command, CommandContext
from ..common_args import add_output_args, add_session_io_args


def _parse_coords(s: str) -> dict:
    """解析坐标字符串 'col,row' 为字典"""
    try:
        col_str, row_str = s.split(",")
        return {"col": int(col_str), "row": int(row_str)}
    except Exception as e:
        raise argparse.ArgumentTypeError(
            f"Invalid coordinates '{s}', expected 'col,row' (1-based): {e}"
        ) from e


class MouseCommand(Command):
    """mouse 命令"""

    name = "mouse"
    help = "发送鼠标动作到 PTY 会话"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("id", help="会话标识")
        parser.add_argument(
            "action",
            choices=[
                "click",
                "drag",
                "scroll",
                "hover",
                "press",
                "grep",
                "_get_cursor_location",
            ],
            help="鼠标动作类型",
        )
        parser.add_argument("args", nargs="*", help="动作位置参数")
        parser.add_argument(
            "--button",
            default="left",
            choices=["left", "right", "middle"],
            help="鼠标按钮（默认 left）",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=1,
            choices=[1, 2, 3],
            help="点击次数（默认 1，仅 click 有效）",
        )
        parser.add_argument("--ctrl", action="store_true", help="按住 Ctrl")
        parser.add_argument("--shift", action="store_true", help="按住 Shift")
        parser.add_argument("--alt", action="store_true", help="按住 Alt")
        parser.add_argument(
            "--grep",
            default=None,
            dest="grep_pattern",
            help="用正则匹配终端屏幕内容获取坐标（多匹配时不执行动作）",
        )
        add_session_io_args(parser)
        add_output_args(parser)

    def validate(self, args, parser: argparse.ArgumentParser) -> None:
        """鼠标动作参数冲突检测：位置参数数量/取值校验"""
        mouse_args = args.args or []
        if args.action == "click":
            if not args.grep_pattern and len(mouse_args) < 1:
                parser.error("click requires <coordinates> (e.g. 10,5) or --grep")
        elif args.action == "hover":
            if not args.grep_pattern and len(mouse_args) < 1:
                parser.error("hover requires <coordinates> (e.g. 10,5) or --grep")
        elif args.action == "scroll":
            if not args.grep_pattern and len(mouse_args) < 1:
                parser.error("scroll requires <coordinates> (e.g. 10,5) or --grep")
            if len(mouse_args) < 2:
                parser.error("scroll requires <direction> (up/down)")
            if mouse_args[1] not in ("up", "down"):
                parser.error("scroll direction must be up or down")
            if len(mouse_args) < 3:
                parser.error("scroll requires <times>")
            try:
                times = int(mouse_args[2])
            except ValueError:
                parser.error("scroll <times> must be an integer")
            if times < 1:
                parser.error("scroll <times> must be >= 1")
        elif args.action == "drag":
            if not args.grep_pattern and len(mouse_args) < 2:
                parser.error(
                    "drag requires <from> <to> coordinates (e.g. 10,5 30,5) or --grep"
                )
        elif args.action == "press":
            if not args.grep_pattern and len(mouse_args) < 2:
                parser.error(
                    "press requires <coordinates> <seconds> (e.g. 10,5 2.0) or --grep"
                )
            if not args.grep_pattern:
                try:
                    duration = float(mouse_args[1])
                except ValueError:
                    parser.error("press <seconds> must be a number")
                if duration <= 0:
                    parser.error("press <seconds> must be > 0")
        elif args.action == "grep":
            if not args.grep_pattern and len(mouse_args) < 1:
                parser.error("grep requires <pattern>")

    def run(self, args, ctx: CommandContext) -> None:
        action = {"action": args.action}
        modifiers = []
        if args.ctrl:
            modifiers.append("ctrl")
        if args.shift:
            modifiers.append("shift")
        if args.alt:
            modifiers.append("alt")
        if modifiers:
            action["modifiers"] = modifiers
        if args.grep_pattern:
            action["grep"] = args.grep_pattern

        mouse_args = args.args or []
        if args.action == "click":
            if not args.grep_pattern:
                action["coords"] = _parse_coords(mouse_args[0])
            action["button"] = args.button
            action["count"] = args.count
        elif args.action == "hover":
            if not args.grep_pattern:
                action["coords"] = _parse_coords(mouse_args[0])
        elif args.action == "scroll":
            if not args.grep_pattern:
                action["coords"] = _parse_coords(mouse_args[0])
            action["direction"] = mouse_args[1]
            action["times"] = int(mouse_args[2])
        elif args.action == "drag":
            if not args.grep_pattern:
                action["coords"] = _parse_coords(mouse_args[0])
                action["to"] = _parse_coords(mouse_args[1])
            action["button"] = args.button
        elif args.action == "press":
            if not args.grep_pattern:
                action["coords"] = _parse_coords(mouse_args[0])
                action["duration"] = float(mouse_args[1])
            action["button"] = args.button
        elif args.action == "grep":
            if not args.grep_pattern:
                action["grep"] = mouse_args[0]
        elif args.action == "_get_cursor_location":
            pass

        ctx.client.cmd_mouse(
            args.id,
            action,
            trigger=args.trigger,
            newline=args.newline,
            timeout=args.timeout,
            encoding=args.encoding,
            keep_ansi=args.keep_ansi,
            idle_timeout=args.idle_timeout,
            idle_after_first_output=args.idle_after_first_output,
            output_path=args.output_path,
            response_format=args.response_format,
            svg_compression_level=args.svg_compression_level,
            snapshot_diff=args.snapshot_diff,
        )
