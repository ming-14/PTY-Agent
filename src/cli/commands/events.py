"""events 命令：查看会话事件"""

import argparse
import time
from datetime import datetime, timezone
from typing import Optional

from ..base import Command, CommandContext


def _maybe_expand_time(s: Optional[str]) -> Optional[str]:
    """补全时间参数：ISO 8601 补本地时区偏移；HH:MM 补当天日期"""
    if s is None:
        return None
    if "T" in s or "-" in s[:5]:
        s = s.replace(" ", "T")
        if "+" not in s and not s.endswith("Z") and len(s) >= 19:
            local_offset = -time.timezone // 3600
            sign = "+" if local_offset >= 0 else "-"
            s += f"{sign}{abs(local_offset):02d}:00"
        return s
    today = datetime.now(tz=timezone.utc).astimezone().date().isoformat()
    return f"{today}T{s}"


class EventsCommand(Command):
    """events 命令"""

    name = "events"
    help = "查看会话事件（默认返回所有事件）"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("id", help="会话标识")
        parser.add_argument(
            "--last", "-l", type=int, default=None, metavar="N", help="仅返回最近 N 条事件"
        )
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            metavar="<ISO时间|HH:MM>",
            help="仅返回此时间之后的事件（支持 ISO 8601 或 HH:MM）",
        )
        parser.add_argument(
            "--until",
            type=str,
            default=None,
            metavar="<ISO时间|HH:MM>",
            help="仅返回此时间之前的事件（支持 ISO 8601 或 HH:MM）",
        )

    def run(self, args, ctx: CommandContext) -> None:
        since = _maybe_expand_time(args.since)
        until = _maybe_expand_time(args.until)
        ctx.client.cmd_events(args.id, last=args.last, since=since, until=until)
