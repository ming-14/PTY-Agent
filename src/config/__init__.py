"""配置模块 —— 按角色（common / daemon / client）从 TOML 文件加载配置

共有配置 → config.common
守护进程配置 → config.daemon（含 common + daemon.toml + logging.toml + web.toml）
客户端配置 → config.client（含 common + client.toml）

使用方式:
    from ..config import common    # 共有常量
    from ..config import daemon    # 守护进程常量（含共有）
    from ..config import client    # 客户端常量（含共有）

    from ..config.common import IS_WINDOWS, DAEMON_HOST
    from ..config.daemon import DEFAULT_DAEMON_PORT, AUTH_TOKEN_NAME
    from ..config.client import CONNECT_TIMEOUT
"""

from . import common, daemon, client

__all__ = ["common", "daemon", "client"]
