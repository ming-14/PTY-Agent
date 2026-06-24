r"""PTY-Agent — 命令行交互式程序交互代理

通过 subprocess 或伪终端（PTY）与交互式 CLI 程序双向通信。
守护进程以独立子进程运行，首次执行命令时自动启动。

子命令: start | stop | list | exec | send | read | kill | events | closewin
"""

import logging
import sys
import argparse
import ctypes
import ctypes.wintypes
from typing import Optional

from .client.transport import Client
from .client.formatter import set_color_mode, set_output_mode, set_debug_mode
from .client.config_manager import ConfigManager
from .daemon.lifecycle import setup_client_logging

_logger = logging.getLogger("pty-client")

# ── 配置键列表（用于 argparse 动态验证）─
_CONFIG_KEYS = (
    "output-by-natural-language",
    "timeout",
    "newline",
    "keep-ansi",
    "encoding",
    "debug",
)


def _parse_default_key(key: str) -> str:
    """将 CLI 中的配置键名转为内部存储键名

    Args:
        key: CLI 配置键名（如 output-by-natural-language）。

    Returns:
        内部存储键名（如 output_by_natural_language）。
    """
    return key.replace("-", "_")


def _format_config_key(key: str) -> str:
    """将内部存储键名转为 CLI 配置键名

    Args:
        key: 内部存储键名（如 output_by_natural_language）。

    Returns:
        CLI 配置键名（如 output-by-natural-language）。
    """
    return key.replace("_", "-")


def _maybe_expand_time(s: Optional[str]) -> Optional[str]:
    """补全简写时间 "HH:MM" 为完整 ISO 8601

    如果输入已是完整 ISO 8601（包含日期部分），直接返回。
    如果形如 "HH:MM" 或 "HH:MM:SS"，自动补全当天日期。

    Args:
        s: 用户输入的时间字符串，可为 None。

    Returns:
        完整 ISO 8601 字符串或 None。
    """
    if s is None:
        return None
    # 如果已包含日期分隔符（T 或空格后的日期部分），视为完整格式
    if "T" in s or "-" in s[:5]:
        # 已是完整 ISO 8601，去除可能存在的空格代替 T 的情况
        s = s.replace(" ", "T")
        # 无时区后缀时加上本地时区偏移
        if "+" not in s and not s.endswith("Z") and len(s) >= 19:
            from datetime import datetime, timezone, timedelta
            # Windows 下 timezone.utc 可用
            local_offset = -time.timezone // 3600
            sign = "+" if local_offset >= 0 else "-"
            s += f"{sign}{abs(local_offset):02d}:00"
        return s
    # 简写 "HH:MM" 或 "HH:MM:SS" → 补全当天日期
    from datetime import date
    today = date.today().isoformat()
    return f"{today}T{s}"


class _HintParser(argparse.ArgumentParser):
    """增强的 ArgumentParser，在常见错误时给出提示"""

    def error(self, message):
        # 检测是否输错了子命令（如直接传了程序路径）
        if "invalid choice" in message:
            import re
            m = re.search(r"'([^']+)'", message)
            if m:
                bad = m.group(1)
                # 看起来像路径/可执行文件
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
    """提示 read 不支持 --timeout 的自定义 Action"""

    def __call__(self, parser, namespace, values, option_string=None):
        parser.error(
            "read 命令不支持 --timeout（读取输出是即时操作，无需等待）\n"
            "若需等待特定输出，请使用: pty-agent send <id> <输入> -t <正则>"
        )


class _InputHintAction(argparse.Action):
    """提示 send/exec 中 -i/--input 不是合法选项，输入应作为位置参数"""

    def __call__(self, parser, namespace, values, option_string=None):
        parser.error(
            f"{option_string} 不是合法选项。发送的文本应作为位置参数直接给出。\n"
            "用法: pty-agent send <会话ID> \"<输入文本>\" [选项]\n"
            "示例: pty-agent send gomoku \"/help\" -t \"提示符>\""
        )


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """为子命令解析器添加通用参数（颜色、输出模式、编码、默认配置）"""
    parser.add_argument("--color", action="store_true", default=False,
                        help="启用终端颜色输出（默认禁用）")
    parser.add_argument("--output-by-natural-language", action="store_true",
                        default=False,
                        help="使用自然语言输出（默认 JSON）")
    parser.add_argument("--encoding", default=None,
                        help="终端编码（如 utf-8、gbk），本次调用记忆")

    parser.add_argument("--default", nargs=2, metavar=("KEY", "VALUE"),
                        default=None,
                        help="设置默认配置 "
                             "(output-by-natural-language/timeout/newline/keep-ansi/encoding/debug)")


def build_parser() -> argparse.ArgumentParser:
    """构建参数解析器"""
    parser = _HintParser(
        prog="pty-agent",
        description="命令行交互式程序交互代理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # 全局选项（顶层，放子命令前后均可）
    parser.add_argument("--show-config", nargs="?", const="", default=None,
                        metavar="KEY",
                        help="查看配置值（不指定 KEY 则显示全部）")
    parser.add_argument("--default", nargs=2, metavar=("KEY", "VALUE"),
                        default=None,
                         help="临时覆盖默认配置 "
                              "(output-by-natural-language/timeout/newline/keep-ansi/encoding/debug)")
    parser.add_argument("--color", action="store_true", default=False,
                        help="启用终端颜色输出（默认禁用）")
    parser.add_argument("--output-by-natural-language", action="store_true",
                        default=False,
                        help="使用自然语言输出（默认 JSON）")
    parser.add_argument("--encoding", default=None,
                        help="终端编码（如 utf-8、gbk），本次调用记忆")
    parser.add_argument("--no-debug", action="store_true", default=False,
                        help="禁用响应中的 debug 输出（进程树/GUI 窗口/事件）")

    sub = parser.add_subparsers(dest="subcmd", help="可用命令")

    p_start = sub.add_parser("start", help="启动后台守护进程")
    _add_common_args(p_start)

    p_stop = sub.add_parser("stop", help="停止后台守护进程")
    _add_common_args(p_stop)

    p_list = sub.add_parser("list", help="列出所有活跃会话")
    _add_common_args(p_list)

    # exec
    p_exec = sub.add_parser("exec", help="启动或附加到会话")
    _add_common_args(p_exec)
    p_exec.add_argument("id", help="会话标识")
    p_exec.add_argument("--command", "-c", default=None,
                        help="要执行的命令字符串（默认经 shell 执行，支持 | && > 等语法）")
    p_exec.add_argument("--pty", action="store_true", default=False,
                        help="启用完整伪终端（自动拆分命令为列表，不支持 shell 语法 | && 等）")
    p_exec.add_argument("--force-pty-mode", action="store_true", default=False,
                        help="强制模式：忽略 --pty 的 shell 操作符检测，原样执行")
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
    p_exec.add_argument("--shell", default=None,
                        choices=["cmd", "powershell", "pwsh", "bash"],
                        help="指定命令解释器（默认 powershell，不可用时回退 cmd；与 --pty 互斥）")

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
    p_send.add_argument("--json-escaping", action="store_true", default=False,
                        help="启用 JSON 转义解码（\\n→换行 \\t→制表符等）；默认 raw 模式原样发送")

    # read
    p_read = sub.add_parser("read", help="读取会话终端输出（无需触发条件）")
    _add_common_args(p_read)
    p_read.add_argument("id", help="会话标识")
    p_read.add_argument("--timeout", type=float, action=_TimeoutHintAction,
                        help=argparse.SUPPRESS)
    p_read.add_argument("--lines", default=None,
                        help="行数过滤: N=最后N行, start:end=范围")
    p_read.add_argument("--grep", default=None,
                        help="正则匹配过滤行")
    p_read.add_argument("--offset", type=int, default=None,
                        help="增量读取：从指定字节偏移开始")
    p_read.add_argument("--full", action="store_true", default=False,
                        help="返回全部累积输出而非仅新输出")
    p_read.add_argument("--keep-ansi", action="store_true", default=None,
                        help="保留终端颜色/样式码（默认过滤；清屏/光标等控制序列始终保留）")

    # kill
    p_kill = sub.add_parser("kill", help="终止指定会话")
    _add_common_args(p_kill)
    p_kill.add_argument("id", help="会话标识")

    # events
    p_events = sub.add_parser("events", help="查看会话事件（默认返回所有事件）")
    _add_common_args(p_events)
    p_events.add_argument("id", help="会话标识")
    p_events.add_argument("--last", type=int, default=None, metavar="N",
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

    return parser


def _handle_config_ops(args) -> Optional[dict]:
    """处理配置管理操作（--default / --show-config / --encoding）

    这些操作在子命令之前或独立执行。

    Args:
        args: 解析后的命令行参数。

    Returns:
        None — 无需继续执行子命令（--show-config 或无子命令时配置操作）。
        dict  — 本次调用中通过 --default 设置的覆盖值（可能为空）。
    """
    cfg = ConfigManager()
    overrides: dict = {}

    # --default KEY VALUE（仅临时覆盖，不持久化）
    default_val = getattr(args, "default", None)
    if default_val is not None:
        key, value = default_val
        internal_key = _parse_default_key(key)
        try:
            cfg.set(internal_key, value)
            overrides[internal_key] = cfg.get(internal_key)
            if args.subcmd is None:
                print(
                    f"警告: --default 仅在与子命令（如 exec/send）配合时有效，"
                    f"单独使用不会产生效果",
                    file=sys.stderr,
                )
                print(f"  已设置临时值: {key} = {value}", file=sys.stderr)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)

    # --show-config [KEY]
    if args.show_config is not None:
        internal_key = _parse_default_key(args.show_config) if args.show_config else None
        show_text = cfg.show(internal_key)
        # 无子命令时追加上下文说明
        if args.subcmd is None:
            show_text += (
                "\n  # 注: 这些默认值仅在有子命令（如 exec/send）时生效，"
                "仅作查询参考"
            )
        print(show_text)
        # --show-config 单独使用时直接退出
        if args.subcmd is None:
            return None

    # 如果只做了配置操作但没有子命令，不需要继续
    handled = default_val is not None
    if handled and args.subcmd is not None:
        return overrides

    # 有子命令但没有配置操作 → 返回空覆盖
    if args.subcmd is not None:
        return overrides

    # 无子命令：已处理了配置操作则退出，否则继续（让 main 打印帮助）
    return None if handled or args.show_config is not None else overrides


def _fix_windows_exec_quoting() -> None:
    """修复 Windows 下嵌套引号导致 exec -c 参数被截断的问题

    当用户从 cmd.exe 执行:
      python app.py exec test -c "python -c \"import time; print(1)\"" ...
    cmd.exe 原样传递 \"，Python 3.12+ 的自定义命令行解析器可能错误拆分，
    导致 -c 只被部分解析。这里使用 Windows 原生 CommandLineToArgvW
    重新解析原始命令行，确保参数正确。

    注意：本修复仅覆盖 cmd.exe 场景。PowerShell 的 \" 不转义（\\为字面量），
    -c 的参数值会被 PowerShell 自身拆分，此时 sys.argv 中 -c 后的值
    已经丢失了嵌套引号内容，CommandLineToArgvW 无法还原。
    PowerShell/pwsh 用户应使用外层单引号 '...' + 内层双引号。
    详见 doc/引号处理规则.md。
    """
    if sys.platform != "win32":
        return

    # 快速判断：只有 exec 子命令且有 -c 参数时可能需要修复
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

    # 检测是否疑似引号被截断：
    # 1) 命令值以反斜杠结尾
    # 2) 命令值包含 "python -c"（嵌套引用的常见模式）
    if not cmd_val.rstrip().endswith('\\') and 'python -c' not in cmd_val:
        return

    try:
        # 获取原始命令行字符串
        kernel32 = ctypes.windll.kernel32
        GetCommandLineW = kernel32.GetCommandLineW
        GetCommandLineW.argtypes = []
        GetCommandLineW.restype = ctypes.wintypes.LPCWSTR
        raw_cmdline = GetCommandLineW()
        if not raw_cmdline:
            return

        # 用 Windows 标准 API 重新解析
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

        # 只有当重新解析后的参数中 -c 的值与当前 sys.argv 不同时才替换
        # （避免无谓的覆盖）
        new_c_idx = None
        for i, arg in enumerate(parsed_argv):
            if arg in ("-c", "--command"):
                new_c_idx = i
                break

        if new_c_idx is not None and new_c_idx + 1 < len(parsed_argv):
            new_cmd_val = parsed_argv[new_c_idx + 1]
            if new_cmd_val != cmd_val and len(new_cmd_val) > len(cmd_val):
                # 新解析的值更长（包含被截断的部分），说明修复成功
                sys.argv = parsed_argv
    except Exception:
        # 任何异常都不影响主流程，降级使用原始 argv
        pass


def main():
    """CLI 入口"""
    setup_client_logging()
    _logger.info("pty-agent CLI 启动, argv=%s", sys.argv)
    # 修复 Windows 下 exec -c 嵌套引号问题（必须在 argparse 之前执行）
    _fix_windows_exec_quoting()

    parser = build_parser()
    args = parser.parse_args()

    # 设置颜色模式和输出模式（各子命令都有这些参数）
    set_color_mode(getattr(args, "color", False))
    set_output_mode(not getattr(args, "output_by_natural_language", False))

    # 处理配置管理操作，获取 --default 设置的临时覆盖值
    config_overrides = _handle_config_ops(args)
    if config_overrides is None:
        # 单独使用 --show-config 或无子命令时退出
        return

    # --no-debug 等价于 --default debug off（全局和子命令级别均可设置）
    if getattr(args, "no_debug", False):
        if "debug" not in config_overrides:
            config_overrides["debug"] = False

    # 设置 debug 输出模式
    debug_enabled = True
    if config_overrides and "debug" in config_overrides:
        debug_enabled = config_overrides["debug"]
    elif getattr(args, "no_debug", False):
        debug_enabled = False
    set_debug_mode(debug_enabled)

    # 无子命令时显示帮助
    if args.subcmd is None:
        parser.print_help()
        return

    # 验证 exec 命令的参数
    if args.subcmd == "exec" and not args.command:
        parser.error("'exec' 命令需要 --command/-c 参数")

    # 验证 idle-after-first-output 的依赖：必须同时有 idle-timeout
    if args.subcmd in ("exec", "send") and args.idle_after_first_output and args.idle_timeout is None:
        print(
            "--idle-after-first-output 需要配合 --idle-timeout 使用，"
            "单独设置无效（当前未启用静默超时检测）",
            file=sys.stderr,
        )

    # events 中 since/until/last 无需 --all 依赖（默认始终返回所有事件）

    # 修正 argparse 对 kill 的处理：第二个 parser 会覆盖第一个
    # 已在构建时修正，这里使用 args.id

    client = Client(config_overrides=config_overrides or None)
    _logger.info("执行命令: %s id=%s", args.subcmd, getattr(args, "id", "N/A"))

    try:
        if args.subcmd == "start":
            client.cmd_start()
        elif args.subcmd == "stop":
            client.cmd_stop()
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
                pty=args.pty,
                force=args.force_pty_mode,
                shell=args.shell,
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
            )
        elif args.subcmd == "read":
            client.cmd_read(
                session_id=args.id,
                lines=args.lines,
                grep=args.grep,
                offset=args.offset,
                encoding=args.encoding,
                full=args.full,
                keep_ansi=args.keep_ansi,
            )
        elif args.subcmd == "kill":
            client.cmd_kill(args.id)
        elif args.subcmd == "events":
            # 处理 HH:MM 简写 → 补齐当天日期
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
    except KeyboardInterrupt:
        if args.output_by_natural_language:
            print("\n操作被用户中断", file=sys.stderr)
        else:
            from .client.formatter import print_response
            print_response({"type": "error", "error": "操作被用户中断"})
        sys.exit(130)
    except Exception as e:
        if args.output_by_natural_language:
            print(f"错误: {e}", file=sys.stderr)
        else:
            from .client.formatter import print_response
            print_response({"type": "error", "error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
