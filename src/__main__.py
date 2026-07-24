r"""PTY-Agent — 命令行交互式程序交互代理

通过伪终端（PTY）与交互式 CLI 程序双向通信。
守护进程以独立子进程运行，首次执行命令时自动启动。

子命令: start | stop | list | exec | send | read | kill | events | closewin | mouse | keygen
"""

import logging
import sys
import argparse
import json
from typing import Optional

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

from .client.transport import Client
from .client.formatter import set_debug_mode
from .client.config_manager import ConfigManager
from .daemon.lifecycle import setup_client_logging
from .protocol.response import Response

_logger = logging.getLogger("pty-client")

_CONFIG_KEYS = (
    "timeout",
    "newline",
    "keep-ansi",
    "encoding",
    "debug",
    "send-eol",
    "response-format",
    "svg-compression-level",
)


def _parse_default_key(key: str) -> str:
    return key.replace("-", "_")


def _format_config_key(key: str) -> str:
    return key.replace("_", "-")


def _maybe_expand_time(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    if "T" in s or "-" in s[:5]:
        s = s.replace(" ", "T")
        if "+" not in s and not s.endswith("Z") and len(s) >= 19:
            from datetime import datetime, timezone, timedelta
            local_offset = -time.timezone // 3600
            sign = "+" if local_offset >= 0 else "-"
            s += f"{sign}{abs(local_offset):02d}:00"
        return s
    from datetime import date
    today = date.today().isoformat()
    return f"{today}T{s}"


class _HintParser(argparse.ArgumentParser):
    def error(self, message):
        if "invalid choice" in message:
            import re
            m = re.search(r"'([^']+)'", message)
            if m:
                bad = m.group(1)
                if any(c in bad for c in ("/", "\\", ".")):
                    print(
                        "\n提示: 如需启动程序，请使用 exec 命令:\n"
                        f"  pty-agent exec my-session -c \"{bad}\"\n"
                        "示例:\n"
                        f"  pty-agent exec build -c \"{bad} --help\" -t \"error\"\n",
                        file=sys.stderr,
                    )
        super().error(message)


class _TimeoutHintAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        parser.error(
            "read 命令不支持 --timeout（读取输出是即时操作，无需等待）\n"
            "若需等待特定输出，请使用: pty-agent send <id> <输入> -t <正则>"
        )


class _InputHintAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        parser.error(
            f"{option_string} 不是合法选项。发送的文本应作为位置参数直接给出。\n"
            "用法: pty-agent send <会话ID> \"<输入文本>\" [选项]\n"
            "示例: pty-agent send gomoku \"/help\" -t \"提示符>\""
        )


def _parse_coords(s: str) -> dict:
    """解析坐标字符串 'col,row' 为字典"""
    try:
        col_str, row_str = s.split(",")
        return {"col": int(col_str), "row": int(row_str)}
    except Exception as e:
        raise argparse.ArgumentTypeError(f"Invalid coordinates '{s}', expected 'col,row' (1-based): {e}") from e


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--encoding", default=None,
                        help="终端编码（如 utf-8、gbk），本次调用记忆")
    parser.add_argument("--default", nargs=2, metavar=("KEY", "VALUE"),
                        action="append", default=None,
                        help="设置默认配置 "
                             "(timeout/newline/keep-ansi/encoding/debug/send-eol/always-return-snapshot/response-format/svg-compression-level/terminal-size)")
    parser.add_argument("--no-debug", action="store_true", default=False,
                        help="禁用响应中的 debugInformation 输出（进程树/GUI 窗口/事件）")


def build_parser() -> argparse.ArgumentParser:
    parser = _HintParser(
        prog="pty-agent",
        description="命令行交互式程序交互代理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument("--show-config", nargs="?", const="", default=None,
                        metavar="KEY",
                        help="查看配置值（不指定 KEY 则显示全部）")
    parser.add_argument("--host", default=None,
                        help="远程 daemon 主机地址（pubkey 跨机 TLS 模式，覆盖 DAEMON_REMOTE_HOST）")
    parser.add_argument("--port", type=int, default=None,
                        help="远程 daemon TLS 端口（pubkey 跨机 TLS 模式，覆盖 DAEMON_REMOTE_PORT）")

    sub = parser.add_subparsers(dest="subcmd", help="可用命令")

    p_set_default = sub.add_parser("set-default", help="覆盖默认配置（会话级）")
    p_set_default.add_argument("key", metavar="KEY",
                               help="配置键名 (timeout/newline/keep-ansi/encoding/debug/send-eol/always-return-snapshot/response-format/svg-compression-level)")
    p_set_default.add_argument("value", metavar="VALUE", help="配置值")

    p_start = sub.add_parser("start", help="启动后台守护进程")
    _add_common_args(p_start)

    p_stop = sub.add_parser("stop", help="停止后台守护进程")
    p_stop.add_argument("--force", "-f", action="store_true",
                        help="强制清理（端口丢失时通过互斥锁定位并终止守护进程）")
    _add_common_args(p_stop)

    p_status = sub.add_parser("status", help="查看守护进程运行状态")
    _add_common_args(p_status)

    p_list = sub.add_parser("list", help="列出所有活跃会话")
    _add_common_args(p_list)

    # exec
    p_exec = sub.add_parser("exec", help="启动或附加到会话")
    _add_common_args(p_exec)
    p_exec.add_argument("id", help="会话标识")
    p_exec.add_argument("--command", "-c", default=None,
                        help="要执行的命令字符串（自动拆分为参数列表执行）")
    p_exec.add_argument("--force-pty-mode", action="store_true", default=False,
                        help="强制模式：忽略 shell 操作符检测，原样拆分执行")
    p_exec.add_argument("--trigger", "-t", default=None,
                        help="触发条件（正则表达式），命中后返回输出")
    p_exec.add_argument("--newline", action="store_true", default=None,
                        help="仅在换行后才检查触发条件（默认取配置值）")
    p_exec.add_argument("--timeout", type=float, default=None,
                        help="等待超时秒数（默认 120，可通过 --default timeout 修改）")
    p_exec.add_argument("--idle-timeout", type=float, default=None,
                        help="输出静默超时（秒）。程序持续 N 秒无新输出时触发返回")
    p_exec.add_argument("--idle-after-first-output", action="store_true", default=False,
                        help="仅在程序首次输出后才开始检测静默超时（初始不检测）")
    p_exec.add_argument("--full", action="store_true", default=False,
                        help="返回全部累积输出而非仅新输出")
    p_exec.add_argument("--keep-ansi", action="store_true", default=None,
                        help="保留终端颜色/样式码（默认过滤；清屏/光标等控制序列始终保留）")
    p_exec.add_argument("--cwd", default=None,
                        help="指定子进程工作目录（默认为守护进程当前目录）")
    p_exec.add_argument("--env", nargs="*", default=None,
                        help="子进程环境变量，格式 KEY=VALUE，可指定多个")
    p_exec.add_argument("--snapshot-mode", action="store_true", default=False,
                        help="快照模式：禁用 trigger/idle-timeout，"
                             "所有输出返回终端屏幕快照而非原始 VT 序列")
    p_exec.add_argument("--size", default=None, metavar="WxH",
                        help="终端尺寸（如 120x40，默认 80x24）")
    p_exec.add_argument("--snapshot-diff", "-s", action="store_true", default=False,
                        help="仅返回屏幕变化的行（需快照模式，stream 格式）")
    p_exec.add_argument("--output", "-o", default=None, dest="output_path",
                        help="输出到文件（.txt/.log=纯文本; .svg=矢量图; .png/.jpg/.bmp=位图，需 Pillow）")
    p_exec.add_argument("--response-format", default=None, choices=["stream", "svg"],
                        dest="response_format",
                        help="响应格式（默认 stream；svg 需屏幕快照模式）")
    p_exec.add_argument("--svg-compression-level", type=int, default=None,
                        choices=[0, 1, 2], dest="svg_compression_level",
                        help="SVG 压缩等级（0=不压缩; 1=轻度; 2=深度，默认）")

    # send
    p_send = sub.add_parser("send", help="向运行中的会话发送输入")
    _add_common_args(p_send)
    p_send.add_argument("id", help="会话标识")
    p_send.add_argument("input", help="要发送的输入文本")
    p_send.add_argument("-i", "--input", action=_InputHintAction,
                        help=argparse.SUPPRESS)
    p_send.add_argument("--trigger", "-t", default=None,
                        help="触发条件（正则表达式），命中后返回输出")
    p_send.add_argument("--newline", action="store_true", default=None,
                        help="仅在换行后才检查触发条件（默认取配置值）")
    p_send.add_argument("--timeout", type=float, default=None,
                        help="等待超时秒数（默认 120，可通过 --default timeout 修改）")
    p_send.add_argument("--idle-timeout", type=float, default=None,
                        help="输出静默超时（秒）。程序持续 N 秒无新输出时触发返回")
    p_send.add_argument("--idle-after-first-output", action="store_true", default=False,
                        help="仅在程序首次输出后才开始检测静默超时（初始不检测）")
    p_send.add_argument("--full", action="store_true", default=False,
                        help="返回全部累积输出而非仅新输出")
    p_send.add_argument("--keep-ansi", action="store_true", default=None,
                        help="保留终端颜色/样式码（默认过滤；清屏/光标等控制序列始终保留）")
    p_send.add_argument("--json-escaping", "-j", action="store_true", default=False,
                        help="启用 JSON + 控制字符转义解码（\\n→换行；{ctrl+a}、{enter}、{up}、{f1} 等生成对应控制字符/VT 序列）；默认 raw 模式原样发送")
    p_send.add_argument("--send-eol", "-e", default=None,
                        choices=["lf", "crlf", "cr", "none"],
                        help="末尾追加的行尾符（默认 cr=\\r，模拟终端 Enter 键；lf=\\n；crlf=\\r\\n；none=不追加）")
    p_send.add_argument("--snapshot", action="store_true", default=False,
                        help="返回终端屏幕快照而非原始 VT 序列输出")
    p_send.add_argument("--snapshot-diff", "-s", action="store_true", default=False,
                        help="仅返回屏幕变化的行（需快照模式，stream 格式）")
    p_send.add_argument("--output", "-o", default=None, dest="output_path",
                        help="输出到文件（.txt/.log=纯文本; .svg=矢量图; .png/.jpg/.bmp=位图，需 Pillow）")
    p_send.add_argument("--response-format", default=None, choices=["stream", "svg"],
                        dest="response_format",
                        help="响应格式（默认 stream；svg 需屏幕快照模式）")
    p_send.add_argument("--svg-compression-level", type=int, default=None,
                        choices=[0, 1, 2], dest="svg_compression_level",
                        help="SVG 压缩等级（0=不压缩; 1=轻度; 2=深度，默认）")

    # read
    p_read = sub.add_parser("read", help="读取会话终端输出")
    _add_common_args(p_read)
    p_read.add_argument("id", help="会话标识")
    p_read.add_argument("--trigger", "-t", default=None,
                        help="触发条件（正则表达式），命中后返回输出")
    p_read.add_argument("--newline", action="store_true", default=None,
                        help="仅在换行后才检查触发条件（默认取配置值）")
    p_read.add_argument("--timeout", type=float, default=None,
                        help="等待超时秒数（默认 120，可通过 --default timeout 修改）")
    p_read.add_argument("--idle-timeout", type=float, default=None,
                        help="输出静默超时（秒）。程序持续 N 秒无新输出时触发返回")
    p_read.add_argument("--idle-after-first-output", action="store_true", default=False,
                        help="仅在程序首次输出后才开始检测静默超时（初始不检测）")
    p_read.add_argument("--lines", "-l", default=None,
                        help="行数过滤: N=最后N行, start:end=范围")
    p_read.add_argument("--grep", "-g", default=None,
                        help="正则匹配过滤行")
    p_read.add_argument("--offset", type=int, default=None,
                        help="增量读取：从指定字节偏移开始")
    p_read.add_argument("--full", action="store_true", default=False,
                        help="返回全部累积输出而非仅新输出")
    p_read.add_argument("--keep-ansi", action="store_true", default=None,
                        help="保留终端颜色/样式码（默认过滤；清屏/光标等控制序列始终保留）")
    p_read.add_argument("--snapshot", action="store_true", default=False,
                        help="返回终端屏幕快照（用户真正看到的终端界面文本，而非原始 VT 序列）")
    p_read.add_argument("--snapshot-diff", "-s", action="store_true", default=False,
                        help="仅返回屏幕变化的行（需快照模式，stream 格式）")
    p_read.add_argument("--output", "-o", default=None, dest="output_path",
                        help="输出到文件（.txt/.log=纯文本; .svg=矢量图; .png/.jpg/.bmp=位图，需 Pillow）")
    p_read.add_argument("--response-format", default=None, choices=["stream", "svg"],
                        dest="response_format",
                        help="响应格式（默认 stream；svg 需屏幕快照模式）")
    p_read.add_argument("--svg-compression-level", type=int, default=None,
                        choices=[0, 1, 2], dest="svg_compression_level",
                        help="SVG 压缩等级（0=不压缩; 1=轻度; 2=深度，默认）")
    p_read.add_argument("--column", type=int, default=None, metavar="N",
                        help="输出第 N 列（1-based，仅 PTY 快照模式）")

    # kill
    p_kill = sub.add_parser("kill", help="终止指定会话")
    _add_common_args(p_kill)
    p_kill.add_argument("id", help="会话标识")

    # events
    p_events = sub.add_parser("events", help="查看会话事件（默认返回所有事件）")
    _add_common_args(p_events)
    p_events.add_argument("id", help="会话标识")
    p_events.add_argument("--last", "-l", type=int, default=None, metavar="N",
                          help="仅返回最近 N 条事件")
    p_events.add_argument("--since", type=str, default=None, metavar="<ISO时间|HH:MM>",
                          help="仅返回此时间之后的事件（支持 ISO 8601 或 HH:MM）")
    p_events.add_argument("--until", type=str, default=None, metavar="<ISO时间|HH:MM>",
                          help="仅返回此时间之前的事件（支持 ISO 8601 或 HH:MM）")

    # closewin
    p_closewin = sub.add_parser("closewin", help="关闭指定 GUI 窗口")
    _add_common_args(p_closewin)
    p_closewin.add_argument("id", help="会话标识")
    p_closewin.add_argument("hwnd", type=lambda x: int(x, 0),
                            help="窗口句柄（十进制或 0x 十六进制）")

    # mouse
    p_mouse = sub.add_parser("mouse", help="发送鼠标动作到 PTY 会话")
    _add_common_args(p_mouse)
    p_mouse.add_argument("id", help="会话标识")
    p_mouse.add_argument("action", choices=["click", "drag", "scroll", "hover", "press", "grep", "_get_cursor_location"],
                         help="鼠标动作类型")
    p_mouse.add_argument("args", nargs="*", help="动作位置参数")
    p_mouse.add_argument("--button", default="left", choices=["left", "right", "middle"],
                         help="鼠标按钮（默认 left）")
    p_mouse.add_argument("--count", type=int, default=1, choices=[1, 2, 3],
                         help="点击次数（默认 1，仅 click 有效）")
    p_mouse.add_argument("--ctrl", action="store_true", help="按住 Ctrl")
    p_mouse.add_argument("--shift", action="store_true", help="按住 Shift")
    p_mouse.add_argument("--alt", action="store_true", help="按住 Alt")
    p_mouse.add_argument("--grep", default=None, dest="grep_pattern",
                         help="用正则匹配终端屏幕内容获取坐标（多匹配时不执行动作）")
    p_mouse.add_argument("--trigger", "-t", default=None,
                         help="触发条件（正则表达式），命中后返回输出")
    p_mouse.add_argument("--newline", action="store_true", default=None,
                         help="仅在换行后才检查触发条件（默认取配置值）")
    p_mouse.add_argument("--timeout", type=float, default=None,
                         help="等待超时秒数（默认 120，可通过 --default timeout 修改）")
    p_mouse.add_argument("--idle-timeout", type=float, default=None,
                         help="输出静默超时（秒）。程序持续 N 秒无新输出时触发返回")
    p_mouse.add_argument("--idle-after-first-output", action="store_true", default=False,
                         help="仅在程序首次输出后才开始检测静默超时（初始不检测）")
    p_mouse.add_argument("--keep-ansi", action="store_true", default=None,
                         help="保留终端颜色/样式码（默认过滤；清屏/光标等控制序列始终保留）")
    p_mouse.add_argument("--snapshot", action="store_true", default=False,
                         help="返回终端屏幕快照而非原始输出")
    p_mouse.add_argument("--snapshot-diff", "-s", action="store_true", default=False,
                         help="仅返回屏幕变化的行（需快照模式，stream 格式）")
    p_mouse.add_argument("--output", "-o", default=None, dest="output_path",
                         help="输出到文件（.txt/.log=纯文本; .svg=矢量图; .png/.jpg/.bmp=位图，需 Pillow）")
    p_mouse.add_argument("--response-format", default=None, choices=["stream", "svg"],
                         dest="response_format",
                         help="响应格式（默认 stream；svg 需屏幕快照模式）")
    p_mouse.add_argument("--svg-compression-level", type=int, default=None,
                         choices=[0, 1, 2], dest="svg_compression_level",
                         help="SVG 压缩等级（0=不压缩; 1=轻度; 2=深度，默认）")

    # wait
    p_wait = sub.add_parser("wait", help="恒等待指定秒数（守护进程侧等待）")
    _add_common_args(p_wait)
    p_wait.add_argument("--timeout", type=float, default=None,
                        help="等待秒数（默认 120，可通过 --default timeout 修改）")

    # keygen
    p_keygen = sub.add_parser(
        "keygen",
        help="生成 Ed25519 公私钥对（用于公私钥认证）",
        description=(
            "生成 Ed25519 密钥对并写入 ~/.pty-agent/keys/，"
            "用于 ENABLE_PUBKEY_AUTH=true 时的非对称认证。\n"
            "生成后需把公钥追加到服务端 ~/.pty-agent/authorized_keys"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_keygen.add_argument(
        "--force", "-f", action="store_true", default=False,
        help="覆盖已存在的密钥文件",
    )
    p_keygen.add_argument(
        "--key-dir", default=None,
        help="密钥目录（默认 ~/.pty-agent/keys）",
    )
    p_keygen.add_argument(
        "--comment", "-C", default=None,
        help="公钥注释（默认 用户名@主机名）",
    )

    return parser


def _handle_config_ops(args) -> Optional[dict]:
    cfg = ConfigManager()
    overrides: dict = {}

    default_vals = getattr(args, "default", None)
    if default_vals is not None:
        for key, value in default_vals:
            internal_key = _parse_default_key(key)
            try:
                cfg.set(internal_key, value)
                overrides[internal_key] = cfg.get(internal_key)
            except ValueError as e:
                from .client.input import safe_print
                safe_print(json.dumps(Response.error(str(e)), ensure_ascii=False))
                sys.exit(1)
        # --default 发送到守护进程按 session UID 存储
        if args.subcmd is None:
            from .client.input import safe_print
            for key, value in default_vals:
                internal_key = _parse_default_key(key)
                safe_print(json.dumps(
                    Response.info(f"已设置默认值: {key} = {cfg.get(internal_key)}（将随会话命令发送到守护进程）"),
                ensure_ascii=False, default=str))

    if args.show_config is not None:
        internal_key = _parse_default_key(args.show_config) if args.show_config else None
        show_text = cfg.show(internal_key)
        from .client.input import safe_print
        safe_print(json.dumps(Response.config(show_text), ensure_ascii=False))
        if args.subcmd is None:
            return None

    handled = default_vals is not None
    if handled and args.subcmd is not None:
        return overrides

    if args.subcmd is not None:
        return overrides

    return None if handled or args.show_config is not None else overrides


def _fix_windows_exec_quoting() -> None:
    if sys.platform != "win32":
        return

    argv = sys.argv
    exec_idx = None
    c_idx = None

    for i, arg in enumerate(argv):
        if arg == "exec":
            exec_idx = i
            break
    if exec_idx is None:
        return

    for i in range(exec_idx + 1, len(argv)):
        if argv[i] in ("-c", "--command"):
            c_idx = i
            break
    if c_idx is None or c_idx + 1 >= len(argv):
        return

    cmd_val = argv[c_idx + 1]

    if not (cmd_val.startswith('"') and cmd_val.endswith('"')):
        return

    try:
        kernel32 = ctypes.windll.kernel32
        GetCommandLineW = kernel32.GetCommandLineW
        GetCommandLineW.argtypes = []
        GetCommandLineW.restype = ctypes.wintypes.LPCWSTR
        raw_cmdline = GetCommandLineW()
        if not raw_cmdline:
            return

        shell32 = ctypes.windll.shell32
        CommandLineToArgvW = shell32.CommandLineToArgvW
        CommandLineToArgvW.argtypes = [
            ctypes.wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_int),
        ]
        CommandLineToArgvW.restype = ctypes.POINTER(ctypes.wintypes.LPWSTR)

        argc = ctypes.c_int(0)
        argv_ptr = CommandLineToArgvW(raw_cmdline, ctypes.byref(argc))

        if not argv_ptr or argc.value < 2:
            return

        try:
            parsed_argv = [argv_ptr[i] for i in range(argc.value)]
        finally:
            LocalFree = kernel32.LocalFree
            LocalFree.argtypes = [ctypes.wintypes.HLOCAL]
            LocalFree(argv_ptr)

        new_c_idx = None
        for i, arg in enumerate(parsed_argv):
            if arg in ("-c", "--command"):
                new_c_idx = i
                break

        if new_c_idx is not None and new_c_idx + 1 < len(parsed_argv):
            new_cmd_val = parsed_argv[new_c_idx + 1]
            if new_cmd_val != cmd_val and len(new_cmd_val) > len(cmd_val):
                sys.argv = parsed_argv
    except Exception:
        pass


def _cmd_keygen(args) -> None:
    """keygen 子命令实现：生成 Ed25519 密钥对并写入文件

    步骤：
    1. 确定密钥目录与文件路径（--key-dir 或默认 ~/.pty-agent/keys）
    2. 检查文件是否已存在（除非 --force 覆盖）
    3. 生成 Ed25519 密钥对（OpenSSH 格式）
    4. 写入私钥文件（Unix 0600 / Windows 跳过权限位）
    5. 写入公钥文件（Unix 0644 / Windows 跳过权限位）
    6. 打印公钥指纹与公钥内容
    7. 提示用户把公钥追加到服务端 authorized_keys

    Args:
        args: argparse Namespace，含 force/key_dir/comment 字段
    """
    import os
    import getpass
    import socket
    from .auth.keys import generate_keypair
    from .client.input import safe_print

    # 确定密钥目录
    if args.key_dir:
        key_dir = os.path.expanduser(args.key_dir)
    else:
        key_dir = os.path.join(os.path.expanduser("~"), ".pty-agent", "keys")

    private_key_path = os.path.join(key_dir, "id_ed25519")
    public_key_path = os.path.join(key_dir, "id_ed25519.pub")

    # 检查文件是否存在（除非 --force）
    if not args.force:
        if os.path.exists(private_key_path):
            safe_print(json.dumps(Response.error(
                f"私钥文件已存在: {private_key_path}\n"
                f"使用 --force 覆盖"
            ), ensure_ascii=False))
            sys.exit(1)
        if os.path.exists(public_key_path):
            safe_print(json.dumps(Response.error(
                f"公钥文件已存在: {public_key_path}\n"
                f"使用 --force 覆盖"
            ), ensure_ascii=False))
            sys.exit(1)

    # 创建目录
    os.makedirs(key_dir, exist_ok=True)

    # 生成密钥对
    _logger.info("开始生成 Ed25519 密钥对到 %s", key_dir)
    private_key = generate_keypair()

    # 写入私钥文件（OpenSSH 格式，无密码）
    private_bytes = private_key.to_openssh_bytes()
    _write_key_file(private_key_path, private_bytes, mode=0o600)

    # 写入公钥文件（OpenSSH authorized_keys 格式）
    comment = args.comment or f"{getpass.getuser()}@{socket.gethostname()}"
    public_line = private_key.public_key.to_ssh_line(comment=comment)
    _write_key_file(public_key_path, (public_line + "\n").encode("utf-8"), mode=0o644)

    fingerprint = private_key.fingerprint
    _logger.info("Ed25519 密钥对已生成: %s", private_key_path)

    # 打印结果
    safe_print(json.dumps({
        "type": "keygen",
        "status": "ok",
        "privateKeyPath": private_key_path,
        "publicKeyPath": public_key_path,
        "fingerprint": fingerprint,
        "publicKey": public_line,
        "comment": comment,
    }, ensure_ascii=False, indent=2))

    # 提示用户追加公钥到 authorized_keys
    authorized_keys_path = os.path.join(os.path.expanduser("~"), ".pty-agent", "authorized_keys")
    print(
        f"\n公钥已生成，请将其追加到服务端 authorized_keys 文件:\n"
        f"  {authorized_keys_path}\n\n"
        f"公钥内容:\n  {public_line}\n\n"
        f"指纹: {fingerprint}",
        file=sys.stderr,
    )


def _write_key_file(path: str, data: bytes, mode: int) -> None:
    """写入密钥文件并设置权限

    Unix: 用 os.open + O_CREAT 设置权限位（避免 umask 影响）
    Windows: 跳过权限位设置（依赖 NTFS ACL）

    Args:
        path: 文件路径
        data: 文件内容字节
        mode: Unix 权限位（如 0o600）
    """
    import os
    if os.name == "nt":
        # Windows: 普通写入，权限由 NTFS ACL 管理
        with open(path, "wb") as f:
            f.write(data)
        _logger.debug("Windows 平台，跳过权限设置: %s", path)
    else:
        # Unix: 用 os.open 设置权限位，避免 umask 干扰
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        _logger.debug("Unix 平台，已设置权限 %o: %s", mode, path)


def main():
    """CLI 入口"""
    setup_client_logging()
    _logger.info("pty-agent CLI 启动, argv=%s", sys.argv)
    _fix_windows_exec_quoting()

    parser = build_parser()
    args = parser.parse_args()

    # keygen 不需要连接守护进程，提前处理
    if args.subcmd == "keygen":
        _cmd_keygen(args)
        return

    config_overrides = _handle_config_ops(args)
    if config_overrides is None:
        return

    if getattr(args, "no_debug", False):
        if "debug" not in config_overrides:
            config_overrides["debug"] = False

    debug_enabled = True
    if config_overrides and "debug" in config_overrides:
        debug_enabled = config_overrides["debug"]
    elif getattr(args, "no_debug", False):
        debug_enabled = False
    set_debug_mode(debug_enabled)

    if args.subcmd is None:
        parser.print_help()
        return

    if args.subcmd == "exec" and not args.command:
        parser.error("'exec' 命令需要 --command/-c 参数")

    if args.subcmd in ("exec", "send") and args.idle_after_first_output and args.idle_timeout is None:
        warn_msg = (
            "--idle-after-first-output 需要配合 --idle-timeout 使用，"
            "单独设置无效（当前未启用静默超时检测）"
        )
        from .client.input import safe_print
        safe_print(json.dumps(Response.warning(warn_msg), ensure_ascii=False))

    is_snapshot_active = False
    if args.subcmd == "exec":
        is_snapshot_active = args.snapshot_mode or bool(config_overrides.get("always_return_snapshot"))
    elif args.subcmd in ("send", "read"):
        is_snapshot_active = args.snapshot or bool(config_overrides.get("always_return_snapshot"))

    if config_overrides.get("svg_compression_level") is not None and not is_snapshot_active:
        from .client.input import safe_print
        safe_print(json.dumps(
            Response.warning("--default svg-compression-level set but snapshot mode not active; hint will not take effect until snapshot mode is enabled"),
        ensure_ascii=False))

    if args.subcmd == "read" and args.snapshot and (args.grep or args.offset or args.full):
        from .client.input import safe_print
        safe_print(json.dumps(
            Response.warning("--snapshot is incompatible with --grep/--offset/--full; snapshot output will be returned, other filters ignored"),
        ensure_ascii=False))

    if args.subcmd == "read" and args.offset and args.full:
        from .client.input import safe_print
        safe_print(json.dumps(
            Response.error("--offset cannot be used with --full"),
        ensure_ascii=False))
        return

    if getattr(args, "snapshot_diff", False):
        if args.subcmd in ("exec", "send") and not is_snapshot_active:
            from .client.input import safe_print
            safe_print(json.dumps(
                Response.error("--snapshot-diff requires snapshot mode (--snapshot-mode for exec; --snapshot for send/read; or --default always-return-snapshot on)"),
            ensure_ascii=False))
            return
        if getattr(args, "response_format", None) == "svg":
            from .client.input import safe_print
            safe_print(json.dumps(
                Response.error("--snapshot-diff is incompatible with --response-format svg"),
            ensure_ascii=False))
            return

    client = Client(
        host=getattr(args, "host", None),
        port=getattr(args, "port", None),
        config_overrides=config_overrides or None,
    )
    _logger.info("执行命令: %s id=%s", args.subcmd, getattr(args, "id", "N/A"))

    try:
        if args.subcmd == "start":
            client.cmd_start()
        elif args.subcmd == "stop":
            client.cmd_stop(force=getattr(args, "force", False))
        elif args.subcmd == "status":
            client.cmd_status()
        elif args.subcmd == "set-default":
            cfg = ConfigManager()
            internal_key = _parse_default_key(args.key)
            try:
                cfg.set(internal_key, args.value)
            except ValueError as e:
                from .client.input import safe_print
                safe_print(json.dumps(Response.error(str(e)), ensure_ascii=False))
                sys.exit(1)
            from .client.input import safe_print
            safe_print(json.dumps(
                Response.info(f"已设置默认值: {args.key} = {cfg.get(internal_key)}（将随会话命令发送到守护进程）"),
            ensure_ascii=False, default=str))
        elif args.subcmd == "list":
            client.cmd_list()
        elif args.subcmd == "exec":
            client.cmd_exec(
                session_id=args.id,
                command=args.command,
                trigger=args.trigger,
                newline=args.newline,
                fresh=True,
                timeout=args.timeout,
                encoding=args.encoding,
                full=args.full,
                keep_ansi=args.keep_ansi,
                idle_timeout=args.idle_timeout,
                idle_after_first_output=args.idle_after_first_output,
                force=args.force_pty_mode,
                cwd=args.cwd,
                env=args.env,
                snapshot_mode=args.snapshot_mode,
                output_path=args.output_path,
                response_format=args.response_format,
                svg_compression_level=args.svg_compression_level,
                snapshot_diff=args.snapshot_diff,
                size=args.size,
            )
        elif args.subcmd == "send":
            client.cmd_send(
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
                snapshot=args.snapshot,
                output_path=args.output_path,
                response_format=args.response_format,
                svg_compression_level=args.svg_compression_level,
                snapshot_diff=args.snapshot_diff,
            )
        elif args.subcmd == "read":
            client.cmd_read(
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
                snapshot=args.snapshot,
                output_path=args.output_path,
                response_format=args.response_format,
                svg_compression_level=args.svg_compression_level,
                snapshot_diff=args.snapshot_diff,
                column=args.column,
            )
        elif args.subcmd == "kill":
            client.cmd_kill(args.id)
        elif args.subcmd == "events":
            since = _maybe_expand_time(args.since)
            until = _maybe_expand_time(args.until)
            client.cmd_events(
                args.id,
                last=args.last,
                since=since,
                until=until,
            )
        elif args.subcmd == "closewin":
            client.cmd_closewin(args.id, args.hwnd)
        elif args.subcmd == "mouse":
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
                    if len(mouse_args) < 1:
                        parser.error("click requires <coordinates> (e.g. 10,5) or --grep")
                    action["coords"] = _parse_coords(mouse_args[0])
                action["button"] = args.button
                action["count"] = args.count
            elif args.action == "hover":
                if not args.grep_pattern:
                    if len(mouse_args) < 1:
                        parser.error("hover requires <coordinates> (e.g. 10,5) or --grep")
                    action["coords"] = _parse_coords(mouse_args[0])
            elif args.action == "scroll":
                if not args.grep_pattern:
                    if len(mouse_args) < 1:
                        parser.error("scroll requires <coordinates> (e.g. 10,5) or --grep")
                    action["coords"] = _parse_coords(mouse_args[0])
                if len(mouse_args) < 2:
                    parser.error("scroll requires <direction> (up/down)")
                if mouse_args[1] not in ("up", "down"):
                    parser.error("scroll direction must be up or down")
                action["direction"] = mouse_args[1]
                if len(mouse_args) < 3:
                    parser.error("scroll requires <times>")
                try:
                    action["times"] = int(mouse_args[2])
                except ValueError:
                    parser.error("scroll <times> must be an integer")
                if action["times"] < 1:
                    parser.error("scroll <times> must be >= 1")
            elif args.action == "drag":
                if not args.grep_pattern:
                    if len(mouse_args) < 2:
                        parser.error("drag requires <from> <to> coordinates (e.g. 10,5 30,5) or --grep")
                    action["coords"] = _parse_coords(mouse_args[0])
                    action["to"] = _parse_coords(mouse_args[1])
                action["button"] = args.button
            elif args.action == "press":
                if not args.grep_pattern:
                    if len(mouse_args) < 2:
                        parser.error("press requires <coordinates> <seconds> (e.g. 10,5 2.0) or --grep")
                    action["coords"] = _parse_coords(mouse_args[0])
                    try:
                        action["duration"] = float(mouse_args[1])
                    except ValueError:
                        parser.error("press <seconds> must be a number")
                    if action["duration"] <= 0:
                        parser.error("press <seconds> must be > 0")
                action["button"] = args.button
            elif args.action == "grep":
                if not args.grep_pattern:
                    if len(mouse_args) < 1:
                        parser.error("grep requires <pattern>")
                    action["grep"] = mouse_args[0]
            elif args.action == "_get_cursor_location":
                pass

            client.cmd_mouse(
                args.id, action,
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
                snapshot=args.snapshot,
                snapshot_diff=args.snapshot_diff,
            )
        elif args.subcmd == "wait":
            client.cmd_wait(timeout=args.timeout)

    except KeyboardInterrupt:
        from .client.formatter import print_response
        print_response(Response.error("Interrupted by user"))
        sys.exit(130)
    except Exception as e:
        from .client.formatter import print_response
        print_response(Response.error(str(e)))
        sys.exit(1)


if __name__ == "__main__":
    main()
