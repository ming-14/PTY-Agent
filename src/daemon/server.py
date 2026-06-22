"""TCP 服务器 — DaemonServer

后台守护进程的 TCP 主循环，负责接受客户端连接并派发给 RequestHandler。
绑定端口后通过共享内存发布端口号。
"""

import os
import sys
import socket
import mmap
import signal
import logging
import threading
from typing import Optional

from ..config import (
    DAEMON_HOST,
    DEFAULT_DAEMON_PORT,
    SOCKET_LISTEN_BACKLOG,
    IS_WINDOWS,
    MMAP_NAME,
    MMAP_SIZE,
    AUTH_TOKEN_ROTATE_INTERVAL,
    AUTH_TOKEN_GRACE_PERIOD,
)
from ..session.shm_utils import write_port_to_shm, generate_auth_token, write_auth_token, cleanup_auth_shm
from ..session.manager import SessionManager
from .handler import RequestHandler

_logger = logging.getLogger("pty-daemon")


class DaemonServer:
    """后台守护进程 TCP 服务器

    负责：
    - TCP 服务器主循环（accept 连接）
    - 绑定成功后通过共享内存发布端口号
    - 信号注册与处理
    - 资源清理（会话停止、PID 文件删除）
    - 认证令牌定时轮换

    Attributes:
        host: 监听地址。
        port: 监听端口。
    """

    def __init__(self, host: str = DAEMON_HOST, port: int = DEFAULT_DAEMON_PORT):
        self.host = host
        self.port = port
        self.manager = SessionManager()
        self._server_socket: Optional[socket.socket] = None
        self._running = False
        self._port_shm: Optional[mmap.mmap] = None  # 保持引用以防销毁
        self._auth_shm: Optional[mmap.mmap] = None  # 认证令牌共享内存
        self._auth_token: str = generate_auth_token()
        self._rotate_timer: Optional[threading.Timer] = None

    def _schedule_rotate(self):
        """安排下一次令牌轮换"""
        self._rotate_timer = threading.Timer(
            AUTH_TOKEN_ROTATE_INTERVAL, self._rotate_token,
        )
        self._rotate_timer.daemon = True
        self._rotate_timer.start()

    def _rotate_token(self):
        """生成新令牌并推送到共享内存和 RequestHandler"""
        old_token = self._auth_token
        self._auth_token = generate_auth_token()
        try:
            self._auth_shm.close()
        except Exception:
            pass
        self._auth_shm = write_auth_token(self._auth_token)
        # 通知 handler 添加新令牌，旧令牌在宽限期内仍有效
        self._handler.add_valid_token(self._auth_token, old_token)
        _logger.info("认证令牌已轮换（旧令牌 %d 秒宽限期）",
                     AUTH_TOKEN_GRACE_PERIOD)
        # 安排下一次轮换
        self._schedule_rotate()

    def run(self):
        """启动服务器主循环"""
        try:
            self._server_socket = socket.socket(
                socket.AF_INET, socket.SOCK_STREAM,
            )
            self._server_socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_REUSEADDR, 1,
            )
            self._server_socket.bind((self.host, self.port))
            # 获取实际端口（如果传入 0 则为随机分配）
            actual_port = self._server_socket.getsockname()[1]
        except OSError as e:
            _logger.error("绑定端口 %s:%s 失败: %s", self.host, self.port, e)
            raise

        # 通过共享内存发布端口号（Windows 用命名 mmap，Unix 回退文件）
        try:
            self._port_shm = write_port_to_shm(actual_port)
            if IS_WINDOWS:
                _logger.info("共享内存已发布端口: %s", actual_port)
        except Exception as e:
            _logger.error("发布端口号失败: %s", e)
            raise

        # 写入认证令牌（与端口同生命周期，销毁后客户端无法连接）
        try:
            self._auth_shm = write_auth_token(self._auth_token)
            if IS_WINDOWS:
                _logger.info("认证令牌已发布")
        except Exception as e:
            _logger.error("写入认证令牌失败: %s", e)
            raise

        self.port = actual_port
        self._server_socket.listen(SOCKET_LISTEN_BACKLOG)
        self._server_socket.settimeout(1.0)
        self._running = True

        _logger.info("守护进程启动，监听 %s:%s", self.host, self.port)

        self._handler = RequestHandler(self.manager, server=self, auth_token=self._auth_token)
        handler = self._handler  # 局部引用线程安全

        # 启动令牌定时轮换
        self._schedule_rotate()

        def _signal_handler(signum, frame):
            _logger.info("收到信号 %s，关闭守护进程...", signum)
            self._running = False
        signal.signal(signal.SIGTERM, _signal_handler)
        if not IS_WINDOWS:
            signal.signal(signal.SIGHUP, _signal_handler)

        try:
            while self._running:
                try:
                    conn, addr = self._server_socket.accept()
                    _logger.debug("接受连接: %s", addr)
                except socket.timeout:
                    continue
                except OSError:
                    break

                t = threading.Thread(
                    target=handler.handle,
                    args=(conn, addr),
                    daemon=True,
                    name=f"conn-{addr}",
                )
                t.start()
        finally:
            self._cleanup()

    def stop(self):
        """停止服务器"""
        self._running = False
        self._cleanup()

    def _cleanup(self):
        """清理资源：停止所有会话 + 关闭 socket + 释放共享内存"""
        self._running = False
        if self._rotate_timer:
            self._rotate_timer.cancel()
            self._rotate_timer = None
        self.manager.stop_all()
        if self._server_socket:
            try:
                self._server_socket.close()
            except OSError:
                pass
            self._server_socket = None
        # 释放共享内存引用（关闭句柄）
        try:
            if self._port_shm:
                self._port_shm.close()
        except (ValueError, OSError):
            pass
        self._port_shm = None
        try:
            if self._auth_shm:
                self._auth_shm.close()
        except (ValueError, OSError):
            pass
        self._auth_shm = None
        _logger.info("守护进程已停止")
