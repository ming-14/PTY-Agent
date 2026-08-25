"""配置模块 —— 按角色（common / shared / daemon / client / sandbox）从 TOML 文件加载配置

配置域归档：
- TOML 数据文件位于 <项目根>/config/（与 src/ 平级；发布形态同规则为 pty-agent/config/），
  由 _loader.py 基于 __file__ 定位，与运行 cwd 无关。
- 配置按侧分离：共享配置（common/shared/transfer.toml）留根目录，
  daemon 专属在 daemon/ 子目录，client 专属在 client/ 子目录。
- 所有 TOML 配置 key 均可被环境变量覆写（PTY_AGENT_<key>，优先级：环境变量 > 文件），
  规则见 _loader.apply_env_overrides 与 config/README.md「环境变量覆写」。
- 代码加载器位于 src/config/，按"每个被加载的 TOML 一个模块"划分：

    common.py   ← common.toml                全项目通用（路径 / 平台 / 输入限制 / 认证开关）
    shared.py   ← common+shared+logging.toml 跨侧共享（协议 / IPC / daemon 控制 / 日志格式/归档/异步队列）
    daemon.py   ← common+daemon/+shared+logging+daemon/logging+web  守护进程侧
    client.py   ← common+shared+logging+client/+client/logging  客户端侧
    sandbox.py  ← daemon/sandbox.toml（可选）  沙箱域（Windows 专属，daemon 侧；文件不存在时 ENABLED=false）
    transfer.py ← transfer.toml              传输协议域（daemon/CLI 两端共享）

- 插件业务参数由插件自包含配置提供（plugin.json config.defaults + 插件目录 config.yaml），不进本目录。

- vnc.toml / vnc.example.toml 不属于本体系：它们是 winvnc.exe 的运行时配置（位于 daemon/ 子目录），
  不经过 _loader.py 加载（Python 侧 VNC 开关在 daemon/web.toml [vnc] 节，见 daemon.py）。

使用方式:
    from ..config import common    # 共有常量
    from ..config import shared    # 跨侧共享常量（含共有）
    from ..config import daemon    # 守护进程常量（含共有+共享）
    from ..config import client    # 客户端常量（含共有+共享）
    from ..config import sandbox   # 沙箱常量（含共有）

    from ..config.common import IS_WINDOWS
    from ..config.shared import AUTH_TOKEN_NAME
    from ..config.client import CONNECT_TIMEOUT, CONNECT_MODE

子模块惰性加载：客户端进程 import src.config 不再急切解析 daemon-only 配置
（daemon.toml / logging.toml / web.toml / sandbox.toml），仅在被访问时加载。
"""

import importlib

_LAZY_MODULES = ("client", "common", "daemon", "sandbox", "shared")

__all__ = list(_LAZY_MODULES)


def __getattr__(name):
    if name in _LAZY_MODULES:
        mod = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
