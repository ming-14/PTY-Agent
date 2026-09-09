"""框架与驱动层：CLI 入口。

用法：
    python -m src <session_id> [--screen <path>] [-o <output>] [--indent N] [--kimi-dir <path>]
    python -m src --list

示例：
    python -m src session_00000000-0000-0000-0000-000000000001
    python -m src session_00000000-0000-0000-0000-000000000001 --screen snapshot.txt
    python -m src session_00000000-0000-0000-0000-000000000001 -o result.json
    python -m src --list
"""
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
    p = argparse.ArgumentParser(
        prog="kimicodeparser",
        description="Kimi Code 会话解析器：从本地存储解析会话状态与消息历史",
    )
    p.add_argument("session_id", nargs="?", help="Kimi Code 会话 ID（session_<UUID>）")
    p.add_argument(
        "--screen", metavar="PATH",
        help="屏幕快照文件路径（VT 文本），提供则解析实时状态",
    )
    p.add_argument(
        "-o", "--output", metavar="PATH",
        help="输出文件路径，不指定则输出到 stdout",
    )
    p.add_argument(
        "--indent", type=int, default=2,
        help="JSON 缩进空格数（默认 2）",
    )
    p.add_argument(
        "--kimi-dir", metavar="PATH",
        help="~/.kimi-code 路径，默认自动检测（KIMI_CODE_HOME 环境变量优先）",
    )
    p.add_argument(
        "--list", action="store_true",
        help="列出全部历史会话（session_id 可选时无效）",
    )
    return p


def _print_sessions(sessions: List[dict]) -> None:
    """以表格打印会话列表。"""
    if not sessions:
        print("(no sessions found)")
        return
    header = f"{'SESSION ID':<48} {'TITLE':<30} {'CWD':<30} {'MODIFIED'}"
    print(header)
    print("-" * min(len(header), 120))
    for s in sessions:
        import datetime
        mtime = s.get("mtime", 0)
        if mtime:
            try:
                mtime_str = datetime.datetime.fromtimestamp(mtime / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
            except (OSError, ValueError):
                mtime_str = str(mtime)
        else:
            mtime_str = "-"
        title = (s.get("title", "") or "")[:28]
        cwd = (s.get("cwd", "") or "")[:28]
        print(f"{s['session_id']:<48} {title:<30} {cwd:<30} {mtime_str}")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 主入口，返回退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    usecase = ParseSessionUseCase(kimi_dir=args.kimi_dir)

    # 列表模式
    if args.list:
        _print_sessions(usecase.list_sessions())
        return 0

    if not args.session_id:
        parser.print_usage(sys.stderr)
        print("error: session_id required (or use --list)", file=sys.stderr)
        return 2

    # 读取屏幕快照（可选）
    screen_text = None
    if args.screen:
        _log.info("reading screen snapshot: %s", args.screen)
        with open(args.screen, "r", encoding="utf-8") as f:
            screen_text = f.read()

    # 执行解析
    try:
        result = usecase.execute(args.session_id, screen_snapshot=screen_text)
    except FileNotFoundError as e:
        _log.error("session not found: %s", e)
        print(f"error: {e}", file=sys.stderr)
        return 1

    # 输出 JSON（stdout 用 UTF-8，避免 Windows GBK 编码问题）
    json_str = output.to_json(result, indent=args.indent)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_str)
        _log.info("output written to %s", args.output)
    else:
        sys.stdout.buffer.write(json_str.encode("utf-8"))
        sys.stdout.buffer.write(b"\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())