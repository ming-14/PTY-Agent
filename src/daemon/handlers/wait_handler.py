"""wait 命令处理器 — 等待通知到达或指定秒数后返回

1. 先检查通知队列（--notify 订阅的通知）：有待消费通知立即返回摘要列表；
2. 无通知时 select.select 同时监听客户端连接与通知唤醒通道（socketpair），
   任一就绪即返回——通知到达（publish 写 1 字节）立即唤醒，无需轮询；
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

    守护进程侧等待：优先返回待消费通知；否则 select 等待（通知唤醒或超时）。
    - 有待消费通知：立即返回通知摘要列表
    - 通知到达：select 被唤醒，返回通知摘要列表
    - 超时：返回 info 响应
    - 客户端断开：立即结束，不发送响应
    """

    def handle(self, ctx: HandlerContext, conn, msg: dict):
        timeout = msg.get("timeout", 120.0)
        _logger.info("wait: timeout=%s", timeout)

        notify_mgr = getattr(getattr(ctx, "server", None), "notify_manager", None)

        # 有待消费通知：立即消费并返回（不等待）
        if notify_mgr is not None:
            pending = notify_mgr.consume_pending()
            if pending:
                elapsed = 0.0
                _logger.info("wait: 有 %d 条待消费通知，立即返回", len(pending))
                Message.send(
                    conn, Response.wait_result(timeout=timeout, elapsed=elapsed,
                                               notifications=pending)
                )
                return

        start = time.monotonic()
        try:
            # select 监听客户端连接 + 通知唤醒通道；通知到达（写 1 字节）即返回
            watch = [conn]
            wake_fd = notify_mgr.wake_fd if notify_mgr is not None else None
            if wake_fd is not None:
                watch.append(wake_fd)
            readable, _, _ = select.select(watch, [], [], timeout)
            elapsed = time.monotonic() - start

            if readable:
                if conn in readable:
                    # 客户端断开或发来数据，提前终止
                    _logger.info("wait: client disconnected after %.1fs", elapsed)
                    return  # 连接已断开，无需发送响应
                if wake_fd in readable and notify_mgr is not None:
                    # 通知到达：清空唤醒字节，消费并返回
                    notify_mgr.drain()
                    pending = notify_mgr.consume_pending()
                    _logger.info(
                        "wait: 通知唤醒 after %.1fs，%d 条待消费", elapsed, len(pending)
                    )
                    Message.send(
                        conn, Response.wait_result(timeout=timeout, elapsed=elapsed,
                                                   notifications=pending)
                    )
                    return
        except (OSError, ValueError) as e:
            _logger.warning("wait: select error: %s", e)
            return

        _logger.info("wait: completed after %.1fs", elapsed)
        Message.send(conn, Response.wait_result(timeout=timeout, elapsed=elapsed))
