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

# 值可能被 shell 嵌套引号截断的带值选项（Windows 下需原生解析纠偏）
_QUOTED_VALUE_OPTIONS = ("-c", "--command", "-i", "--input")


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


def _add_common_args(parser: argparse.ArgumentParser,
                     top_level: bool = False) -> None:
    """为顶层与子命令解析器添加同一组通用选项（放子命令前后均可）

    三处 help 文案只在此定义一次，顶层与子命令不会各说各话。
    子命令侧一律 `default=SUPPRESS`：未显式给出时不写命名空间，
    因此放在子命令前面的同名选项值不会被覆盖。

    Args:
        parser:    目标解析器（顶层或子命令）。
        top_level: True 表示顶层解析器（提供真实默认值）。
    """
    sup = argparse.SUPPRESS
    parser.add_argument("--show-config", nargs="?", const="",
                        default=None if top_level else sup,
                        metavar="KEY",
                        help="查看配置值（不指定 KEY 则显示全部）")
    parser.add_argument("--default", nargs=2, metavar=("KEY", "VALUE"),
                        default=None if top_level else sup,
                        help="临时覆盖默认配置 "
                             "(timeout/newline/debug/send-eol)")
    parser.add_argument("--no-debug", action="store_true",
                        default=False if top_level else sup,
                        help="禁用响应中的 debug 输出（进程树/GUI 窗口/事件；"
                             "本轮因 GUI 窗口返回时窗口信息仍输出）")


def build_parser() -> argparse.ArgumentParser:
    """构建参数解析器"""
    parser = _HintParser(
        prog="pty-agent",
        description="命令行交互式程序交互代理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # 全局选项（与每个子命令共用同一组定义，放子命令前后均可）
    _add_common_args(parser, top_level=True)

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
                        choices=["cmd", "sh", "powershell", "pwsh", "bash"],
                        help="指定命令解释器（仅 subprocess 模式，与 --pty 互斥；"
                             "取值按平台校验，平台不支持时报错。"
                             "默认：Windows powershell（不可用回退 cmd）/ POSIX /bin/sh）")
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


def _find_quoted_option(argv, options=_QUOTED_VALUE_OPTIONS):
    """返回 argv 中第一个目标选项的下标（`--opt=value` 内联形式同样命中）

    Args:
        argv:    参数列表。
        options: 目标选项名集合。

    Returns:
        下标；未找到返回 None。
    """
    for i, token in enumerate(argv):
        if token.partition("=")[0] in options:
            return i
    return None


def _option_value(argv, index):
    """取 argv[index] 处选项的值：内联 `--opt=v` 取等号后部分，否则取下一个 token"""
    if index is None or index >= len(argv):
        return None
    token = argv[index]
    if "=" in token:
        return token.partition("=")[2]
    return argv[index + 1] if index + 1 < len(argv) else None


def _adopt_native_argv(argv, parsed_argv, options=_QUOTED_VALUE_OPTIONS):
    """判断是否应采用原生解析结果替代 Python 解析的 argv

    判据：目标选项的值在原生解析中**不同且更长**——shell 引号处理差异只会把
    值截短，不会变长，因此"更长"即原 argv 被截断的特征。
    命中时返回 parsed_argv（整体替换：被错误拆开的多余 token 也随之消失）。

    Args:
        argv:       Python 收到的 argv。
        parsed_argv: CommandLineToArgvW 解析出的 argv。
        options:    受关注的带值选项名集合。

    Returns:
        需要采用时返回 parsed_argv，否则 None。
    """
    if len(parsed_argv) < 2:
        return None
    old_val = _option_value(argv, _find_quoted_option(argv, options))
    new_val = _option_value(parsed_argv, _find_quoted_option(parsed_argv, options))
    if not old_val or not new_val:
        return None
    if new_val == old_val or len(new_val) <= len(old_val):
        return None
    return parsed_argv


def _fix_windows_quoting() -> None:
    """修复 Windows 下嵌套引号导致 exec -c / send -i 参数值被截断的问题

    当用户从 cmd.exe 执行:
      python app.py exec test -c "python -c \\"import time; print(1)\\"" ...
    cmd.exe 原样传递 \\"，Python 的命令行解析器可能错误拆分，导致 -c/-i 只被
    部分解析。这里使用 Windows 原生 CommandLineToArgvW 重新解析原始命令行，
    必要时整体采用其结果（见 _adopt_native_argv 的判据）。

    注意：本修复仅覆盖 cmd.exe 场景。PowerShell 的 \\" 不转义（\\\\为字面量），
    参数值会被 PowerShell 自身拆分，此时 sys.argv 中的值已经丢失了嵌套引号
    内容，CommandLineToArgvW 无法还原。PowerShell/pwsh 用户应使用外层单引号
    '...' + 内层双引号。详见 docs/Skill文档/引号处理规则.md。
    """
    if sys.platform != "win32":
        return
    if _find_quoted_option(sys.argv) is None:
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

        adopted = _adopt_native_argv(sys.argv, parsed_argv)
        if adopted is not None:
            sys.argv = adopted
    except Exception:
        # 任何异常都不影响主流程，降级使用原始 argv
        pass


def main():
    """CLI 入口"""
    setup_client_logging()
    # 修复 Windows 下 -c / -i 嵌套引号被截断的问题（必须在 argparse 之前执行）
    _fix_windows_quoting()

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

    # --shell 只对 subprocess 后端有意义：pty 模式下命令不经 shell 执行，
    # 静默忽略会让用户以为命令跑在指定 shell 里，故直接报错（文档声明互斥）。
    if args.subcmd == "exec" and args.pty and args.shell:
        parser.error("--pty 与 --shell 互斥：真实终端模式不经 shell 执行命令")

    # --force-pty-mode 仅在 --pty 下生效，单独使用无意义（与 idle-after-first-output
    # 一样只提示不报错：不会导致错误行为，只是选项被忽略）
    if args.subcmd == "exec" and args.force_pty_mode and not args.pty:
        print(
            "--force-pty-mode 需要配合 --pty 使用，subprocess 模式下无效（已忽略）",
            file=sys.stderr,
        )

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
