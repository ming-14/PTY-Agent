"""守护进程入口（`python -m src.daemon`）

支持前台运行模式：`python -m src.daemon --foreground`
（或环境变量 PTY_AGENT_FOREGROUND=1），供 s6/systemd 等服务监督器管理。
"""

import sys

from .lifecycle import main

main(sys.argv[1:])
