"""共享参数组与辅助

组合式提供：add_common_args（全命令）、add_session_io_args 与 add_output_args
（exec/send/read/mouse 复用）。含配置键转换、idle 警告与提前退出辅助。
"""

import argparse
import json

from ..client.input import safe_print
from ..protocol.response import Response


def _parse_default_key(key: str) -> str:
    """CLI 配置键（连字符）转内部键（下划线）"""
    return key.replace("-", "_")


def _format_config_key(key: str) -> str:
    """内部配置键（下划线）转 CLI 展示键（连字符）"""
    return key.replace("_", "-")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """全命令公共参数：--encoding / --default / --debug-output（由 registry 统一添加）"""
    parser.add_argument(
        "--encoding", default=None, help="终端编码（如 utf-8、gbk），本次调用记忆"
    )
    parser.add_argument(
        "--default",
        nargs=2,
        metavar=("KEY", "VALUE"),
        action="append",
        default=None,
        help="设置默认配置 "
        "(timeout/newline/keep-ansi/encoding/debug/send-eol/response-format/svg-compression-level/terminal-size)",
    )
    parser.add_argument(
        "--debug-output",
        action="store_true",
        default=False,
        help="响应中输出 debugInformation（进程树/GUI 窗口/事件），默认关闭",
    )


def add_session_io_args(parser: argparse.ArgumentParser) -> None:
    """会话 IO 参数组：触发/超时/静默/快照差异（exec/send/read/mouse 复用）"""
    parser.add_argument(
        "--trigger", "-t", default=None, help="触发条件（正则表达式），命中后返回输出"
    )
    parser.add_argument(
        "--newline",
        action="store_true",
        default=None,
        help="仅在换行后才检查触发条件（默认取配置值）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="等待超时秒数（默认 120，可通过 --default timeout 修改）",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=None,
        help="输出静默超时（秒）。程序持续 N 秒无新输出时触发返回",
    )
    parser.add_argument(
        "--idle-after-first-output",
        action="store_true",
        default=False,
        help="仅在程序首次输出后才开始检测静默超时（初始不检测）",
    )
    parser.add_argument(
        "--keep-ansi",
        action="store_true",
        default=None,
        help="保留终端颜色/样式码（默认过滤；清屏/光标等控制序列始终保留）",
    )
    parser.add_argument(
        "--snapshot-diff",
        "-s",
        action="store_true",
        default=False,
        help="仅返回屏幕变化的行（需快照模式，stream 格式）",
    )


def add_output_args(parser: argparse.ArgumentParser) -> None:
    """输出参数组：文件输出 / 响应格式 / SVG 压缩（exec/send/read/mouse 复用）"""
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        dest="output_path",
        help="输出到文件（.txt/.log=纯文本; .svg=矢量图; .png/.jpg/.bmp=位图，需 Pillow）",
    )
    parser.add_argument(
        "--response-format",
        default=None,
        choices=["stream", "svg"],
        dest="response_format",
        help="响应格式（默认 stream；svg 需屏幕快照模式）",
    )
    parser.add_argument(
        "--svg-compression-level",
        type=int,
        default=None,
        choices=[0, 1, 2],
        dest="svg_compression_level",
        help="SVG 压缩等级（0=不压缩; 1=轻度; 2=深度，默认）",
    )


def warn_idle_without_idle_timeout(args) -> None:
    """exec/send 共用警告：--idle-after-first-output 缺 --idle-timeout 时提示（不阻断）"""
    if args.idle_after_first_output and args.idle_timeout is None:
        warn_msg = (
            "--idle-after-first-output 需要配合 --idle-timeout 使用，"
            "单独设置无效（当前未启用静默超时检测）"
        )
        safe_print(json.dumps(Response.warning(warn_msg), ensure_ascii=False))


def abort_error(message: str) -> None:
    """打印 JSON 错误并提前退出（等价原 main 的 safe_print + return，退出码 0）"""
    safe_print(json.dumps(Response.error(message), ensure_ascii=False))
    raise SystemExit(0)
