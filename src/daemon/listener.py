"""TCP 监听器 — 封装单端口的 accept 循环

每个 Listener 负责一个 (host, port, transport, auth_context) 组合。
TCP/TLS 传输层在此封装，accept 后派发给 DaemonDispatcher。

依赖规则：框架层对象，依赖 AuthContext（框架层）和 DaemonDispatcher（处理入口）。
"""

import socket
import ssl
import threading
from typing import Callable, Optional

from ..auth.context import AuthContext
from ..config.daemon import (
    CONNECTION_READ_TIMEOUT,
    IS_WINDOWS,
    MAX_CONNECTIONS,
    SOCKET_LISTEN_BACKLOG,
)
from ..logging import get_logger

_logger = get_logger("pty-daemon")

# 全局并发连接槽位：所有 Listener 实例共享（跨 basic/token/tls 端口合计上限），
# 超限时 accept 后立即拒绝，防止 Slowloris 占满连接线程与内存
_CONNECTION_SLOTS = threading.BoundedSemaphore(MAX_CONNECTIONS)


class Listener:
    """TCP 监听器 — 封装单端口的 accept 循环

    每个 Listener 负责一个 (host, port, transport, auth_context) 组合。
    TCP/TLS 传输层在此封装，accept 后派发给 DaemonDispatcher。

    连接级防护（Slowloris DoS）：
    - 读超时：连接在 _handle_connection 开头设置 CONNECTION_READ_TIMEOUT，
      TLS 握手也受此超时约束（wrap_socket 继承底层 socket 超时语义）。
    - 连接数上限：全局 MAX_CONNECTIONS 槽位（跨所有 Listener 共享），
      超限时 accept 后立即关闭并记录 warning，不建线程。

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
        if IS_WINDOWS:
            # Windows 的 SO_REUSEADDR 允许两个进程绑定同一端口（bind/listen 均
            # 静默成功但端口实际不可用，netstat 无监听器）；SO_EXCLUSIVEADDRUSE
            # 正确拒绝已占用端口（10048），且不影响 TIME_WAIT 快速重启重绑
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is not None:
                self._sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        else:
            # Unix：SO_REUSEADDR 仅允许 TIME_WAIT 重绑（快速重启），无双绑定问题
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
            handler_factory: 接收 AuthContext，返回 DaemonDispatcher 实例。
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
        连接数上限在此检查：超限时立即关闭，不进入线程（槽位全局共享）。
        """
        while self._running:
            sock = self._sock
            if sock is None:
                break
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    _logger.warning(
                        "Listener [%s] accept 异常", self._transport, exc_info=True
                    )
                break

            # 连接数上限：全局槽位满则拒绝（关闭套接字让对端立即感知），
            # 不创建线程，防止慢客户端占满 daemon 线程/内存
            if not _CONNECTION_SLOTS.acquire(blocking=False):
                _logger.warning(
                    "Listener [%s] 连接数达上限 (%d)，拒绝连接: %s",
                    self._transport,
                    MAX_CONNECTIONS,
                    addr,
                )
                try:
                    conn.close()
                except OSError:
                    pass
                continue

            _logger.debug("Listener [%s] 接受连接: %s", self._transport, addr)

            t = threading.Thread(
                target=self._handle_connection,
                args=(conn, addr),
                daemon=True,
                name=f"conn-{addr}",
            )
            t.start()

    def _handle_connection(self, conn, addr):
        """连接线程入口：读超时 + TLS 包装（慢握手在此执行）+ 派发给 handler

        读超时在 TLS 包装前设置：wrap_socket 继承底层 socket 的超时语义，
        慢握手（客户端延迟 ClientHello）同样受超时约束，不再永久占线程。
        超时仅作用于"读请求"阶段——dispatcher 为单次 recv 模型，recv 完成
        后 handler 处理不涉及 socket；响应发送前 dispatcher 恢复无超时
        （见 dispatcher.handle），避免大响应被写超时误杀。
        """
        try:
            conn.settimeout(CONNECTION_READ_TIMEOUT)
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
        finally:
            # 释放连接槽位（accept 时 acquire；无论正常/异常路径都归还）
            _CONNECTION_SLOTS.release()

    def stop(self):
        """停止 accept 线程，关闭 socket

        已 accept 的连接不在此强制关闭：由 dispatcher.handle 的 finally
        负责关闭（读超时也会让慢连接在 CONNECTION_READ_TIMEOUT 内退出），
        连接槽位随线程结束归还。
        """
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        _logger.debug("Listener [%s] 已停止", self._transport)
