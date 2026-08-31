"""框架与驱动层：CLI 入口。"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .adapters import output
from .infra.logging import get_logger
from .usecases import ParseSessionUseCase

_log = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="smartparser", description="smartagent 子代理会话解析器")
    p.add_argument("session_id", nargs="?", help="会话 ID")
    p.add_argument("--screen", metavar="PATH", help="屏幕快照文件路径（VT 文本）")
    p.add_argument("-o", "--output", metavar="PATH", help="输出文件路径")
    p.add_argument("--indent", type=int, default=2, help="JSON 缩进空格数（默认 2）")
    p.add_argument("--data-dir", metavar="PATH", help="会话根目录，默认 temp 自动检测")
    p.add_argument("--list", action="store_true", help="列出全部历史会话")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    usecase = ParseSessionUseCase(data_dir=args.data_dir)

    if args.list:
        sessions = usecase.list_sessions()
        if not sessions:
            print("(no sessions found)")
            return 0
        header = f"{'SESSION ID':<40} {'MODIFIED':<20}"
        print(header)
        print("-" * len(header))
        import datetime
        for s in sessions:
            mtime = datetime.datetime.fromtimestamp(s["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
            print(f"{s['session_id']:<40} {mtime:<20}")
        return 0

    if not args.session_id:
        parser.print_usage(sys.stderr)
        print("error: session_id required (or use --list)", file=sys.stderr)
        return 2

    screen_text = None
    if args.screen:
        with open(args.screen, "r", encoding="utf-8") as f:
            screen_text = f.read()

    try:
        result = usecase.execute(args.session_id, screen_snapshot=screen_text)
    except FileNotFoundError as e:
        _log.error("session not found: %s", e)
        print(f"error: {e}", file=sys.stderr)
        return 1

    json_str = output.to_json(result, indent=args.indent)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_str)
    else:
        sys.stdout.buffer.write(json_str.encode("utf-8"))
        sys.stdout.buffer.write(b"\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())