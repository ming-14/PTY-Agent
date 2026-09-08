"""框架与驱动层：CLI 入口。

用法：
    python -m src <session_id> [--screen <path>] [-o <output>] [--indent N] [--data-dir <path>]
    python -m src --list
    python -m src --list-running

示例：
    python -m src 716187c5-046d-46a4-b8ce-00a33067b620
    python -m src e22e38e0f182bbfe44db216bfd430f3e
    python -m src e22e38e --screen snapshot.txt
    python -m src 716187c5 -o result.json
    python -m src --list
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from typing import List, Optional

from .adapters import output
from .infra.logging import get_logger
from .usecases import ParseSessionUseCase

_log = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="crushparser",
        description="Crush 会话解析器：从本地 SQLite 存储解析会话状态与消息历史",
    )
    p.add_argument("session_id", nargs="?",
                    help="Crush 会话 ID（UUID / XXH3 hash / hash 前缀）")
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
        "--data-dir", metavar="PATH",
        help="Crush 数据目录（含 crush.db），默认自动检测 projects.json",
    )
    p.add_argument(
        "--list", action="store_true",
        help="列出全部历史会话（session_id 可选时无效）",
    )
    p.add_argument(
        "--list-running", action="store_true",
        help="列出运行中会话",
    )
    return p


def _print_sessions(sessions: List[dict]) -> None:
    """以表格打印会话列表。"""
    if not sessions:
        print("(no sessions found)")
        return
    header = f"{'SESSION ID':<40} {'TITLE':<40} {'UPDATED'}"
    print(header)
    print("-" * len(header))
    for s in sessions:
        sid = s.get("id") or s.get("session_id") or ""
        title = (s.get("title") or "")[:38]
        ts = s.get("updated_at") or 0
        try:
            tstr = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            tstr = ""
        print(f"{sid:<40} {title:<40} {tstr}")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 主入口，返回退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    usecase = ParseSessionUseCase(data_dir=args.data_dir)

    # 列表模式
    if args.list:
        _print_sessions(usecase.list_sessions())
        return 0
    if args.list_running:
        for s in usecase.list_running():
            print(json.dumps(s, ensure_ascii=False, indent=2))
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
    except (FileNotFoundError, KeyError) as e:
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