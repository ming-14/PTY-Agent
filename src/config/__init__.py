"""配置模块 —— 按角色（common / daemon / client / sandbox）从 TOML 文件加载配置

配置域归档：
- TOML 数据文件位于 <项目根>/config/（与 src/ 平级；发布形态同规则为 pty-agent/config/），
  由 _loader.py 基于 __file__ 定位，与运行 cwd 无关。
- 代码加载器位于 src/config/，按"每个被加载的 TOML 一个模块"划分：

    common.py   ← common.toml             全项目通用（路径 / 平台 / 输入限制 / 认证开关）
    daemon.py   ← daemon+logging+web 合并  守护进程侧（四文件一锅端，含日志与 Web 配置）
    client.py   ← common+client.toml      客户端侧
    sandbox.py  ← sandbox.toml            沙箱域（Windows 专属）
    files.py    ← files.toml              文件工具域

- vnc.toml / vnc.example.toml 不属于本体系：它们是 winvnc.exe 的运行时配置，
  不经过 _loader.py 加载（Python 侧 VNC 开关在 web.toml [vnc] 节，见 daemon.py）。

使用方式:
    from ..config import common    # 共有常量
    from ..config import daemon    # 守护进程常量（含共有）
    from ..config import client    # 客户端常量（含共有）
    from ..config import sandbox   # 沙箱常量（含共有）

    from ..config.common import IS_WINDOWS, DAEMON_HOST
    from ..config.daemon import DEFAULT_DAEMON_PORT, AUTH_TOKEN_NAME
    from ..config.client import CONNECT_TIMEOUT
"""

from . import common, daemon, client, sandbox

__all__ = ["common", "daemon", "client", "sandbox"]
