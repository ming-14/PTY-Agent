"""wait 命令处理器 — 恒等待指定秒数后返回

使用 select.select() 监听 socket 可读性 + 超时，
避免 time.sleep() 阻塞线程且无法检测客户端断开。
客户端断开连接时立即返回，不浪费线程资源。
"""

import select
import time

from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler
from ...execution.context import HandlerContext
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class WaitHandler(DaemonHandler):
    """wait 命令处理器

    在守护进程侧等待指定秒数后返回响应。
    使用 select 实现可中断等待：
    - 正常超时：返回 info 响应
    - 客户端断开：立即结束，不发送响应
    """

    def handle(self, ctx: HandlerContext, conn, msg: dict):
        timeout = msg.get("timeout", 120.0)
        _logger.info("wait: timeout=%s", timeout)

        start = time.monotonic()
        try:
            # select 等待 timeout 秒，或直到 socket 可读（客户端断开）
            readable, _, _ = select.select([conn], [], [], timeout)
            elapsed = time.monotonic() - start

            if readable:
                # 客户端断开或发来数据，提前终止
                _logger.info("wait: client disconnected after %.1fs", elapsed)
                return  # 连接已断开，无需发送响应
        except (OSError, ValueError) as e:
            _logger.warning("wait: select error: %s", e)
            return

        _logger.info("wait: completed after %.1fs", elapsed)
        Message.send(conn, Response.wait_result(timeout=timeout, elapsed=elapsed))
