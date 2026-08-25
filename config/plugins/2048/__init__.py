"""2048 插件 — exec 命令改写为执行插件自带 2048 游戏

kind=cli（见同目录 plugin.json）：客户端进程内执行（daemon 不加载）。
before_request 钩子在 exec 请求发送前把 command 改写为
"<解释器> <插件目录>/main.py"，timeout 与 trigger 取插件配置，使
`app.py exec <sid> -c "任意内容" --plugin 2048` 直接进入 2048 游戏会话：

- 玩家经 send 发送方向键/WASD 控制移动，q/quit 退出
- 游戏退出（玩家退出或 Ctrl+C）时在恢复主屏幕后输出 "quit" 标记，
  exec 按 trigger "quit" 匹配返回；timeout 为等待上限
- -c 原始内容被替换丢弃，sid 保留

解释器：config.interpreter（默认当前客户端进程 python，sys.executable，
保证与客户端同机一致；跨机 daemon 场景可用配置覆盖）。
"""

import logging
import os
import sys

from src.plugins.base import Plugin

_logger = logging.getLogger("pty-client")

DEFAULT_TIMEOUT = 10
DEFAULT_TRIGGER = "quit"


class Game2048Plugin(Plugin):
    """2048 游戏入口改写插件（元信息见同目录 plugin.json）"""

    def before_request(self, ctx, msg: dict):
        # 仅改写 exec 请求；其他命令（send/read/mouse 自动挂钩）放行
        if msg.get("type") != "exec":
            return None
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(plugin_dir, "main.py")
        interpreter = sys.executable
        timeout, trigger = DEFAULT_TIMEOUT, DEFAULT_TRIGGER
        if ctx.config is not None:
            interpreter = ctx.config.get("interpreter") or interpreter
            timeout = ctx.config.get("timeout", DEFAULT_TIMEOUT)
            trigger = ctx.config.get("trigger", DEFAULT_TRIGGER)
        msg["command"] = [interpreter, script]
        msg["timeout"] = timeout
        # 与命令行 --timeout 等价的显式等待标志（无 trigger 时也进入等待模式）
        msg["explicit_timeout"] = True
        msg["trigger"] = trigger
        _logger.info(
            "2048 插件改写 exec: sid=%r timeout=%r trigger=%r",
            msg.get("id"), timeout, trigger,
        )
        return msg


plugin = Game2048Plugin
