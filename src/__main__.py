r"""PTY-Agent — 命令行交互式程序交互代理

通过 subprocess 管道或真实终端（TTY）与交互式 CLI 程序双向通信。
守护进程以独立子进程运行，首次执行命令时自动启动。

架构：CLI（参数解析）→ client.api.PtyClient（构建请求 → 共享内存往返）
      → client.presenters（呈现）

子命令: start | stop | list | exec | send | read | remove | closewin
"""

import sys
import argparse

from .client.api import PtyClient
from .client.config_manager import ConfigManager
from .client.controller import setup_client_logging
from .client.presenters import print_response


def _parse_default_key(key: str) -> str:
    """将 CLI 中的配置键名转为内部存储键名

    Args:
        key: CLI 配置键名（如 idle-timeout）。

    Returns:
        内部存储键名（如 idle_timeout）。
    """
    return key.replace("-", "_")


def _format_config_key(key: str) -> str:
    """将内部存储键名转为 CLI 配置键名

    Args:
        key: 内部存储键名（如 idle_timeout）。

    Returns:
        CLI 配置键名（如 idle-timeout）。
    """
    return key.replace("_", "-")


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


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """为子命令解析器添加通用参数（默认配置）"""
    # SUPPRESS：子解析器不覆盖全局 --default（否则放在子命令前的 --default 会丢失）
    parser.add_argument("--default", nargs=2, metavar=("KEY", "VALUE"),
                        default=argparse.SUPPRESS,
                        help="设置默认配置 "
                             "(timeout/newline/debug/send-eol)")


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
                             "(timeout/newline/debug)")
    parser.add_argument("--no-debug", action="store_true", default=False,
                        help="禁用响应中的 debug 输出（进程树/GUI 窗口/事件；"
                             "本轮因 GUI 窗口返回时窗口信息仍输出）")

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
                        help="启用真实终端（命令拆为列表执行，不支持 shell 语法 | && 等）")
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
                        help="返回全量输出而非仅增量输出")
    p_exec.add_argument("--shell", default=None,
                        choices=["cmd", "powershell", "pwsh", "bash"],
                        help="指定命令解释器（默认 powershell，不可用时回退 cmd；仅 subprocess 模式，与 --pty 互斥）")
    p_exec.add_argument("--cwd", default=None,
                        help="指定子进程工作目录（默认为守护进程当前目录）")

    # send
    p_send = sub.add_parser("send", help="向运行中的会话发送输入")
    _add_common_args(p_send)
    p_send.add_argument("id", help="会话标识")
    p_send.add_argument("--input", "-i", default=None, metavar="<content>",
                        help="要发送的输入文本（必填，原样发送不转义；"
                             "以 - 开头时用 --input=<content> 形式）")
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
                        help="返回全量输出而非仅增量输出")
    p_send.add_argument("--send-eol", default=None,
                        choices=["lf", "cr", "crlf"],
                        help="行尾样式：lf(\\n 默认) / cr(\\r) / crlf(\\r\\n)")

    # read
    p_read = sub.add_parser("read", help="读取会话终端输出")
    _add_common_args(p_read)
    p_read.add_argument("id", help="会话标识")
    p_read.add_argument("--lines", default=None,
                        help="行数过滤(基于全量输出): N=最后N行, start:end=范围")
    p_read.add_argument("--grep", default=None,
                        help="正则匹配过滤行(基于全量输出)")
    p_read.add_argument("--full", action="store_true", default=False,
                        help="返回全量输出（默认返回可见屏幕（pty 模式）/完整缓冲（subprocess 模式））")

    # remove
    p_remove = sub.add_parser("remove", help="移除指定会话")
    _add_common_args(p_remove)
    p_remove.add_argument("id", help="会话标识")

    # closewin
    p_closewin = sub.add_parser("closewin", help="关闭指定 GUI 窗口")
    _add_common_args(p_closewin)
    p_closewin.add_argument("id", help="会话标识")
    p_closewin.add_argument("hwnd", type=lambda x: int(x, 0),
                            help="窗口句柄（十进制或 0x 十六进制）")

    return parser


def _handle_config_ops(args) -> dict | None:
    """处理配置管理操作（--default / --show-config）

    Args:
        args: 解析后的命令行参数。

    Returns:
        None — 无需继续执行子命令。
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
                warn_msg = (
                    f"警告: --default 仅在与子命令（如 exec/send）配合时有效，"
                    f"单独使用不会产生效果\n  已设置临时值: {key} = {value}"
                )
                print(warn_msg, file=sys.stderr)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)

    # --show-config [KEY]
    if args.show_config is not None:
        internal_key = _parse_default_key(args.show_config) if args.show_config else None
        show_text = cfg.show(internal_key)
        if args.subcmd is None:
            show_text += (
                "\n  # 注: 这些默认值仅在有子命令（如 exec/send）时生效，"
                "仅作查询参考"
            )
        print(show_text)
        if args.subcmd is None:
            return None

    if args.subcmd is not None:
        return overrides
    handled = default_val is not None
    return None if handled or args.show_config is not None else overrides


def _fix_windows_exec_quoting() -> None:
    """修复 Windows 下嵌套引号导致 exec -c 参数被截断的问题

    当用户从 cmd.exe 执行:
      python app.py exec test -c "python -c \\"import time; print(1)\\"" ...
    cmd.exe 原样传递 \\"，Python 3.12+ 的自定义命令行解析器可能错误拆分，
    导致 -c 只被部分解析。这里使用 Windows 原生 CommandLineToArgvW
    重新解析原始命令行，确保参数正确。

    注意：本修复仅覆盖 cmd.exe 场景。PowerShell 的 \\" 不转义（\\\\为字面量），
    -c 的参数值会被 PowerShell 自身拆分，此时 sys.argv 中 -c 后的值
    已经丢失了嵌套引号内容，CommandLineToArgvW 无法还原。
    PowerShell/pwsh 用户应使用外层单引号 '...' + 内层双引号。
    详见 docs/Skill文档/引号处理规则.md。
    """
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

    # 检测是否疑似引号被截断：
    # 1) 命令值以反斜杠结尾
    # 2) 命令值包含 "python -c"（嵌套引用的常见模式）
    if not cmd_val.rstrip().endswith('\\') and 'python -c' not in cmd_val:
        return

    try:
        import ctypes
        import ctypes.wintypes
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
        # 任何异常都不影响主流程，降级使用原始 argv
        pass


def main():
    """CLI 入口"""
    setup_client_logging()
    # 修复 Windows 下 exec -c 嵌套引号问题（必须在 argparse 之前执行）
    _fix_windows_exec_quoting()

    parser = build_parser()
    args = parser.parse_args()

    # 处理配置管理操作，获取 --default 设置的临时覆盖值
    config_overrides = _handle_config_ops(args)
    if config_overrides is None:
        return

    # --no-debug 等价于 --default debug off
    if getattr(args, "no_debug", False):
        if "debug" not in config_overrides:
            config_overrides["debug"] = False

    # 计算 debug 呈现开关
    debug_enabled = True
    if config_overrides and "debug" in config_overrides:
        debug_enabled = config_overrides["debug"]
    elif getattr(args, "no_debug", False):
        debug_enabled = False

    # 无子命令时显示帮助
    if args.subcmd is None:
        parser.print_help()
        return

    # 验证 exec 命令的参数
    if args.subcmd == "exec" and not args.command:
        parser.error("'exec' 命令需要 --command/-c 参数")

    # 验证 send 命令的参数（空字符串是合法输入：只提交一个行尾）
    if args.subcmd == "send" and args.input is None:
        parser.error("'send' 命令需要 --input/-i 参数")

    # 验证 idle-after-first-output 的依赖：必须同时有 idle-timeout
    if args.subcmd in ("exec", "send") and args.idle_after_first_output and args.idle_timeout is None:
        warn_msg = (
            "--idle-after-first-output 需要配合 --idle-timeout 使用，"
            "单独设置无效（当前未启用静默超时检测）"
        )
        print(warn_msg, file=sys.stderr)

    client = PtyClient(config_overrides=config_overrides or None)

    def _present(resp: dict):
        print_response(resp, show_debug=debug_enabled)

    try:
        if args.subcmd == "start":
            client.cmd_start()
        elif args.subcmd == "stop":
            client.cmd_stop()
        elif args.subcmd == "list":
            _present(client.cmd_list())
        elif args.subcmd == "exec":
            _present(client.cmd_exec(
                session_id=args.id,
                command=args.command,
                trigger=args.trigger,
                newline=args.newline,
                fresh=True,
                timeout=args.timeout,
                full=args.full,
                idle_timeout=args.idle_timeout,
                idle_after_first_output=args.idle_after_first_output,
                pty=args.pty,
                force=args.force_pty_mode,
                shell=args.shell,
                cwd=args.cwd,
            ))
        elif args.subcmd == "send":
            _present(client.cmd_send(
                session_id=args.id,
                input_text=args.input,
                trigger=args.trigger,
                newline=args.newline,
                fresh=True,
                timeout=args.timeout,
                full=args.full,
                idle_timeout=args.idle_timeout,
                idle_after_first_output=args.idle_after_first_output,
                send_eol=args.send_eol,
            ))
        elif args.subcmd == "read":
            _present(client.cmd_read(
                session_id=args.id,
                lines=args.lines,
                grep=args.grep,
                full=args.full,
            ))
        elif args.subcmd == "remove":
            _present(client.cmd_remove(args.id))
        elif args.subcmd == "closewin":
            _present(client.cmd_closewin(args.id, args.hwnd))
    except KeyboardInterrupt:
        print("\n操作被用户中断", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        import logging
        logging.getLogger("pty-client").exception("命令执行异常")
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
