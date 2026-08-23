"""命令行交互式程序交互代理

通过伪终端（PTY）与交互式 CLI 程序双向通信。
守护进程以独立子进程运行，首次执行命令时自动启动。

子命令: start | stop | list | exec | send | advsend | read | kill | events | closewin | mouse | attend | keygen
"""

import sys

from ..client.presenter import error_seen, present
from ..client.result import ErrorResult
from ..client.lifecycle import setup_client_logging
from ..client.transport import Client
from ..logging import get_logger
from .base import CommandContext
from .commands import register_all
from .pipeline import (
    apply_config_ops,
    check_common_conflicts,
    resolve_debug_mode,
    setup_cli_plugins,
)
from .registry import CommandRegistry
from .windows import fix_windows_exec_quoting

_logger = get_logger("pty-client")


def main() -> None:
    """CLI 入口"""
    setup_client_logging()
    _logger.info("pty-agent CLI 启动, argv=%s", sys.argv)
    fix_windows_exec_quoting()

    registry = CommandRegistry()
    register_all(registry)
    parser = registry.build_parser(
        prog="pty-agent",
        description="命令行交互式程序交互代理",
        epilog=__doc__,
    )

    args = parser.parse_args()

    config_overrides = apply_config_ops(args, parser)
    if config_overrides is None:
        return

    resolve_debug_mode(args, config_overrides)

    if args.subcmd is None:
        parser.print_help()
        return

    if not check_common_conflicts(args):
        return

    cmd = registry.get(args.subcmd)
    ctx = CommandContext(parser=parser, config_overrides=config_overrides)
    if cmd.needs_client:
        ctx.cli_plugins = setup_cli_plugins()
        ctx.client = Client(
            config_overrides=config_overrides or None,
            cli_plugins=ctx.cli_plugins,
        )

    _logger.info("执行命令: %s id=%s", args.subcmd, getattr(args, "id", "N/A"))

    try:
        registry.dispatch(args, ctx)
        # 业务错误（error 响应）提升为进程退出码 1（缺参/用法错误为 argparse 的 2）
        if error_seen():
            sys.exit(1)
    except KeyboardInterrupt:
        present(ErrorResult(message="Interrupted by user"))
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        present(ErrorResult(message=str(e)))
        sys.exit(1)
