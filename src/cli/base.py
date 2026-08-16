"""命令基类与命令上下文

CLI 命令子系统的基础契约：每个命令实现 Command 子类，声明参数与执行逻辑，
由 CommandRegistry 统一注册、构建解析器并派发。与 daemon 侧 handlers/ 的
每命令一处理器模式对称。
"""

import argparse
from typing import Optional

from ..client.cli_plugins import CliPluginHost
from ..client.transport import Client


class CommandContext:
    """命令执行上下文

    Attributes:
        parser: 顶层 argparse parser（供命令内 ctx.parser.error() 使用，含友好提示）
        client: 已构建的 Client 实例；本地命令（needs_client=False）为 None
        cli_plugins: CLI 插件宿主；本地命令或初始化失败为 None
        config_overrides: --default 产生的配置覆盖
    """

    def __init__(
        self,
        parser: argparse.ArgumentParser,
        client: Optional[Client] = None,
        cli_plugins: Optional[CliPluginHost] = None,
        config_overrides: Optional[dict] = None,
    ) -> None:
        self.parser = parser
        self.client = client
        self.cli_plugins = cli_plugins
        self.config_overrides = config_overrides


class Command:
    """CLI 子命令基类

    子类声明 name/help/description 并实现 add_arguments() 与 run()；
    语义冲突检测放 validate()，由 registry 在 run 前统一调用。
    """

    name: str = ""
    help: str = ""
    description: Optional[str] = None
    # 是否使用公共参数（--encoding/--default/--debug-output）；本地命令为 False
    use_common_args: bool = True
    # 是否需要连接守护进程（含 CLI 插件初始化）；本地命令为 False
    needs_client: bool = True
    # 子命令 parser 的 formatter_class（None 沿用顶层布局）
    formatter_class = None

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """向子命令 parser 声明本命令参数；无附加参数的命令无需覆写"""

    def validate(self, args, parser: argparse.ArgumentParser) -> None:
        """调用期语义冲突检测；冲突时 parser.error() 或打印 JSON 错误后退出"""

    def run(self, args, ctx: CommandContext) -> None:
        """执行命令；args 映射到 client.cmd_* 调用或本地逻辑"""
        raise NotImplementedError
