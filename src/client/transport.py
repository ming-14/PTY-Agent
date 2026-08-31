"""TCP 传输层 — Client 类

封装与守护进程的 TCP 通信，向 CLI 入口提供简洁的命令接口。
支持自动启动守护进程、临时默认配置覆盖。

实现按职责拆分到混入类（各模块见 client/ 包）：
- connection：三路分流连接、消息往返与认证装配（ClientConnectionMixin）
- defaults：调用级默认配置应用与会话默认值回填（ClientDefaultsMixin）
- plugin_route：CLI 插件挂载分流与会话挂载自动挂钩（ClientPluginMixin）
- commands：会话命令 cmd_*（ClientCommandsMixin）
- file_commands：文件命令 cmd_file_*（ClientFileCommandsMixin）
- workflow_commands：workflow 命令 cmd_workflow_*（ClientWorkflowCommandsMixin）
响应渲染统一走 presenter.print_response（presenter.py）。
"""

from typing import Optional

from .cli_plugins import CliPluginHost
from .commands import ClientCommandsMixin
from .config_manager import ConfigManager
from .connection import ClientConnectionMixin
from .defaults import ClientDefaultsMixin
from .file_commands import ClientFileCommandsMixin
from .plugin_route import ClientPluginMixin
from .workflow_commands import ClientWorkflowCommandsMixin


class Client(
    ClientConnectionMixin,
    ClientDefaultsMixin,
    ClientPluginMixin,
    ClientCommandsMixin,
    ClientFileCommandsMixin,
    ClientWorkflowCommandsMixin,
):
    """前端客户端，封装与守护进程的 TCP 通信（职责经混入类拆分）

    连接方式由 client.toml [connection] 的 CONNECT_MODE 决定，
    与 daemon 侧 [listener] 对应监听器匹配：
    - "basic": 直接连接 BASIC_HOST:BASIC_PORT，密码认证（BASIC_PASSWORD 空则无认证，不自动启动 daemon）
    - "token": 连接本机 TOKEN_HOST:TOKEN_PORT，SHM 发现 + Token/HMAC 认证
              （daemon 未运行时自动启动，本机同机场景）
    - "tls":   连接 TLS_HOST:TLS_PORT，TLS 传输 + TOFU 证书验证 + Ed25519 认证
    """

    def __init__(
        self,
        config_overrides: Optional[dict] = None,
        cli_plugins: Optional[CliPluginHost] = None,
        plugin_options: Optional[dict] = None,
    ):
        """初始化客户端

        Args:
            config_overrides: 配置覆盖字典。
            cli_plugins: CLI 插件宿主（CliPluginHost）；None 表示不启用。
            plugin_options: 本次调用显式提供的插件选项 {插件 id: {选项名: 值}}；
                非空时注入 exec/send/read/mouse 消息（msg.pluginOptions）。
        """
        self._config = ConfigManager(overrides=config_overrides)
        # 凭证提供者懒加载：首次 _connect 时由 _load_signer_and_providers() 装配
        # providers 只有 0 或 1 个：单 provider / None（basic 无认证）
        self._credential_provider = None
        self._cli_plugins = cli_plugins
        self.plugin_options = plugin_options or {}