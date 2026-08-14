"""配置模块 —— 按角色（common / shared / daemon / client / sandbox）从 TOML 文件加载配置

配置域归档：
- TOML 数据文件位于 <项目根>/config/（与 src/ 平级；发布形态同规则为 pty-agent/config/），
  由 _loader.py 基于 __file__ 定位，与运行 cwd 无关。
- 配置按侧分离：共享配置（common/shared/transfer.toml）留根目录，
  daemon 专属在 daemon/ 子目录，client 专属在 client/ 子目录。
- 代码加载器位于 src/config/，按"每个被加载的 TOML 一个模块"划分：

    common.py   ← common.toml                全项目通用（路径 / 平台 / 输入限制 / 认证开关）
    shared.py   ← common+shared.toml         跨侧共享（协议 / IPC / daemon 控制 / 日志格式）
    daemon.py   ← common+daemon/+shared+logging+/web+ 守护进程侧
    client.py   ← common+shared+client/      客户端侧
    sandbox.py  ← daemon/sandbox.toml（可选）  沙箱域（Windows 专属，daemon 侧；文件不存在时 ENABLED=false）
    transfer.py ← transfer.toml              传输协议域（daemon/CLI 两端共享）

- 插件业务参数由插件自包含配置提供（如 config/plugins/files/files.toml），不进本目录。

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
"""

from . import client, common, daemon, sandbox, shared

__all__ = ["client", "common", "daemon", "sandbox", "shared"]
