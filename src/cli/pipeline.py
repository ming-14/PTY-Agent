"""CLI 公共管线

封装配置操作（--default / --show-config）、debug 开关、CLI 插件初始化与
跨命令通用校验；cli/main 只负责编排。
"""

import sys
from typing import Optional

from ..client.config_manager import ConfigManager
from ..client.presenter import emit, emit_error, set_debug_mode, set_render_hook
from .common_args import _parse_default_key
from ..logging import get_logger

_logger = get_logger("pty-client")


def apply_config_ops(args, parser) -> Optional[dict]:
    """处理 --default / --show-config，返回配置覆盖

    返回 None 表示应提前退出（纯配置操作）；返回 dict 表示继续执行命令。
    """
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
                emit_error(str(e))
                sys.exit(1)
        # --default 发送到守护进程按 session UID 存储
        if args.subcmd is None:
            for key, value in default_vals:
                internal_key = _parse_default_key(key)
                emit(
                    f"已设置默认值: {key} = {cfg.get(internal_key)}"
                    "（将随会话命令发送到守护进程）"
                )

    if args.show_config is not None:
        internal_key = _parse_default_key(args.show_config) if args.show_config else None
        show_text = cfg.show(internal_key)
        emit(show_text, msg_type="config")
        if args.subcmd is None:
            return None

    handled = default_vals is not None
    if handled and args.subcmd is not None:
        return overrides
    if args.subcmd is not None:
        return overrides
    return None if handled or args.show_config is not None else overrides


def resolve_debug_mode(args, config_overrides: dict) -> None:
    """解析 debug 开关：--debug-output / --default debug，写入 presenter 全局状态（默认关闭）"""
    if getattr(args, "debug_output", False):
        if "debug" not in config_overrides:
            config_overrides["debug"] = True
    debug_enabled = False
    if config_overrides and "debug" in config_overrides:
        debug_enabled = config_overrides["debug"]
    set_debug_mode(debug_enabled)


def setup_cli_plugins():
    """初始化 CLI 插件宿主（Plugin.kind=cli）

    与 daemon 插件同一注册体系（plugins.json），在客户端进程内加载执行；
    初始化失败仅跳过，不影响命令执行。
    """
    try:
        from ..client.cli_plugins import CliPluginHost
        from ..client.presenter import set_render_hook
        from ..config.plugins import PLUGIN_PATHS as _cli_plugin_paths

        cli_plugins = None
        if _cli_plugin_paths:
            cli_plugins = CliPluginHost(_cli_plugin_paths)
            if not cli_plugins.is_empty():
                set_render_hook(cli_plugins.render_hook)
    except Exception:
        _logger.exception("CLI 插件初始化失败，本次调用不启用")
        cli_plugins = None
    return cli_plugins


def check_common_conflicts(args) -> bool:
    """通用跨命令冲突校验；冲突时打印错误并返回 False（main 提前返回）"""
    if getattr(args, "snapshot_diff", False):
        if getattr(args, "response_format", None) == "svg":
            emit_error("--snapshot-diff is incompatible with --response-format svg")
            return False
    return True
