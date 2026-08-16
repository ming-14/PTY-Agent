"""TCP 监听器 — 封装单端口的 accept 循环

每个 Listener 负责一个 (host, port, transport, auth_context) 组合。
TCP/TLS 传输层在此封装，accept 后派发给 RequestHandler。

依赖规则：框架层对象，依赖 AuthContext（框架层）和 RequestHandler（接口适配器层）。
"""

import socket
import ssl
import threading
from typing import Callable, Optional

from ..auth.context import AuthContext
from ..config.daemon import SOCKET_LISTEN_BACKLOG
from ..logging import get_logger

_logger = get_logger("pty-daemon")


class Listener:
    """TCP 监听器 — 封装单端口的 accept 循环

    每个 Listener 负责一个 (host, port, transport, auth_context) 组合。
    TCP/TLS 传输层在此封装，accept 后派发给 RequestHandler。

    生命周期：
        1. bind()  — 创建 socket 并绑定端口（检测端口冲突），返回实际端口
        2. start() — 开始监听，启动 accept 线程
        3. stop()  — 停止 accept 线程，关闭 socket

    Attributes:
        transport: 传输类型 "tcp" 或 "tls"
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
        self._transport = transport  # "tcp" or "tls"
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
        # accept 超时定期唤醒：Unix 上另一线程 close() 不会中断阻塞中的
        # accept（Windows closesocket 会），需超时醒来检查停止标志
        self._sock.settimeout(0.5)
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

        循环依赖 0.5s accept 超时退出：stop() 置 _running=False 后，
        下一次超时醒来即退出（Unix 的 close 不中断阻塞中的 accept）。
        TLS 握手在连接线程内执行（_handle_connection）：慢握手
        （客户端延迟 ClientHello）不阻塞 accept 循环与其他连接。
        """
        while self._running:
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    _logger.warning(
                        "Listener [%s] accept 异常", self._transport, exc_info=True
                    )
                break

            _logger.debug("Listener [%s] 接受连接: %s", self._transport, addr)

            t = threading.Thread(
                target=self._handle_connection,
                args=(conn, addr),
                daemon=True,
                name=f"conn-{addr}",
            )
            t.start()

    def _handle_connection(self, conn, addr):
        """连接线程入口：TLS 包装（慢握手在此执行）+ 派发给 handler"""
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
                try:
                    conn.close()
                except OSError:
                    pass
                return
        self._handler.handle(conn, addr)

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
