"""TCP 监听器 — 封装单端口的 accept 循环

每个 Listener 负责一个 (host, port, transport, auth_context) 组合。
明文/TLS 传输层在此封装，accept 后派发给 RequestHandler。

依赖规则：框架层对象，依赖 AuthContext（框架层）和 RequestHandler（接口适配器层）。
"""

import logging
import socket
import ssl
import threading
from typing import Callable, Optional

from ..auth.context import AuthContext
from ..config.daemon import SOCKET_LISTEN_BACKLOG

_logger = logging.getLogger("pty-daemon")


class Listener:
    """TCP 监听器 — 封装单端口的 accept 循环

    每个 Listener 负责一个 (host, port, transport, auth_context) 组合。
    明文/TLS 传输层在此封装，accept 后派发给 RequestHandler。

    生命周期：
        1. bind()  — 创建 socket 并绑定端口（检测端口冲突），返回实际端口
        2. start() — 开始监听，启动 accept 线程
        3. stop()  — 停止 accept 线程，关闭 socket

    Attributes:
        transport: 传输类型 "plain" 或 "tls"
    """

    def __init__(
        self,
        host: str,
        port: int,
        transport: str,
        auth_context: AuthContext,
        ssl_context: Optional[ssl.SSLContext] = None,
    ):
        self._host = host
        self._port = port
        self._transport = transport  # "plain" or "tls"
        self._auth_context = auth_context
        self._ssl_context = ssl_context
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._handler = None
        self._running = False

    @property
    def transport(self) -> str:
        return self._transport

    @property
    def port(self) -> int:
        """实际监听端口（bind 后可用，port=0 时由内核分配）"""
        if self._sock is not None:
            return self._sock.getsockname()[1]
        return self._port

    def bind(self) -> int:
        """创建 socket 并绑定端口，返回实际端口

        不开始监听，仅绑定。用于端口冲突检测和获取实际端口号。
        """
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        actual_port = self._sock.getsockname()[1]
        _logger.debug(
            "Listener [%s] 绑定 %s:%d",
            self._transport,
            self._host,
            actual_port,
        )
        return actual_port

    def start(self, handler_factory: Callable[[AuthContext], object]):
        """开始监听并启动 accept 线程

        Args:
            handler_factory: 接收 AuthContext，返回 RequestHandler 实例。
                             在此调用一次，之后所有连接复用同一 handler。
        """
        self._sock.listen(SOCKET_LISTEN_BACKLOG)
        self._handler = handler_factory(self._auth_context)
        self._running = True
        self._thread = threading.Thread(
            target=self._accept_loop,
            daemon=True,
            name=f"listener-{self._transport}",
        )
        self._thread.start()
        _logger.info(
            "Listener [%s] 监听 %s:%d",
            self._transport,
            self._host,
            self.port,
        )

    def _accept_loop(self):
        """accept 循环，每个连接创建处理线程

        socket 无 timeout，靠 stop() 关闭 socket 触发 OSError 退出循环。
        """
        while self._running:
            try:
                conn, addr = self._sock.accept()
            except OSError:
                if self._running:
                    _logger.warning(
                        "Listener [%s] accept 异常", self._transport, exc_info=True
                    )
                break

            _logger.debug("Listener [%s] 接受连接: %s", self._transport, addr)

            # TLS 包装（tls 传输且提供 ssl_context 时生效）
            if self._transport == "tls" and self._ssl_context is not None:
                try:
                    conn = self._ssl_context.wrap_socket(conn, server_side=True)
                except Exception:
                    _logger.warning(
                        "Listener [%s] TLS 握手失败: %s",
                        self._transport,
                        addr,
                        exc_info=True,
                    )
                    conn.close()
                    continue

            t = threading.Thread(
                target=self._handler.handle,
                args=(conn, addr),
                daemon=True,
                name=f"conn-{addr}",
            )
            t.start()

    def stop(self):
        """停止 accept 线程，关闭 socket"""
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        _logger.debug("Listener [%s] 已停止", self._transport)
