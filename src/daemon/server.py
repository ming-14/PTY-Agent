"""TCP 服务器 — DaemonServer

后台守护进程的 TCP 主循环，负责接受客户端连接并派发给 RequestHandler。
绑定端口后通过共享内存发布 PID+端口号。
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
from ..session.shm_utils import (
    write_daemon_info_to_shm,
    read_daemon_info_from_shm,
    generate_auth_token,
    write_auth_token,
    cleanup_auth_shm,
)
from ..session.manager import SessionManager
from .handler import RequestHandler

_logger = logging.getLogger("pty-daemon")


class DaemonServer:
    """后台守护进程 TCP 服务器

    负责：
    - TCP 服务器主循环（accept 连接）
    - 绑定成功后通过共享内存发布 PID+端口号
    - 信号注册与处理
    - 资源清理（会话停止、共享内存释放）
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
        self._cleaned_up = False
        self._port_shm: Optional[mmap.mmap] = None
        self._auth_shm: Optional[mmap.mmap] = None
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
        self._handler.add_valid_token(self._auth_token, old_token)
        _logger.info("认证令牌已轮换（旧令牌 %d 秒宽限期）",
                     AUTH_TOKEN_GRACE_PERIOD)
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
            actual_port = self._server_socket.getsockname()[1]
        except OSError as e:
            _logger.error("绑定端口 %s:%s 失败: %s", self.host, self.port, e)
            raise

        existing = read_daemon_info_from_shm()
        if existing is not None:
            existing_pid, existing_port = existing
            from .lifecycle import _pid_exists, _ping_daemon
            if _pid_exists(existing_pid) and _ping_daemon(existing_port):
                _logger.error(
                    "守护进程已在运行 (PID:%d 端口:%d)，拒绝覆盖共享内存",
                    existing_pid, existing_port,
                )
                self._server_socket.close()
                raise RuntimeError(
                    f"守护进程已在运行 (PID:{existing_pid} 端口:{existing_port})"
                )

        # 写入认证令牌（必须在端口信息之前，防止客户端在 token 就绪前连接）
        try:
            self._auth_shm = write_auth_token(self._auth_token)
            if IS_WINDOWS:
                _logger.info("认证令牌已发布")
        except Exception as e:
            _logger.error("写入认证令牌失败: %s", e)
            raise

        try:
            self._port_shm = write_daemon_info_to_shm(os.getpid(), actual_port)
            if IS_WINDOWS:
                _logger.info("共享内存已发布 PID:%d 端口:%d", os.getpid(), actual_port)
        except Exception as e:
            _logger.error("发布守护进程信息失败: %s", e)
            raise

        self.port = actual_port
        self._my_shm_signature = f"{os.getpid()}:{actual_port}"
        self._server_socket.listen(SOCKET_LISTEN_BACKLOG)
        self._server_socket.settimeout(1.0)
        self._running = True

        _logger.info("守护进程启动，监听 %s:%s", self.host, self.port)

        self._handler = RequestHandler(self.manager, server=self, auth_token=self._auth_token)
        handler = self._handler

        self._schedule_rotate()

        def _signal_handler(signum, frame):
            _logger.info("收到信号 %s，关闭守护进程...", signum)
            self._running = False
        signal.signal(signal.SIGTERM, _signal_handler)
        if not IS_WINDOWS:
            signal.signal(signal.SIGHUP, _signal_handler)

        try:
            while self._running:
                if not self._verify_shm():
                    _logger.error("共享内存被覆盖，检测到另一个守护进程启动，自动退出")
                    break
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

    def _verify_shm(self) -> bool:
        """检查共享内存是否仍属于当前守护进程。

        如果共享内存被另一个实例覆盖，返回 False，调用方应退出。
        """
        try:
            info = read_daemon_info_from_shm()
            if info is None:
                return True
            current = f"{info[0]}:{info[1]}"
            if current != self._my_shm_signature:
                _logger.warning(
                    "共享内存变更: 期望 %s，实际 %s",
                    self._my_shm_signature, current,
                )
                return False
        except Exception:
            pass
        return True

    def _cleanup(self):
        """清理资源：停止所有会话 + 关闭 socket + 释放共享内存"""
        if self._cleaned_up:
            return
        self._cleaned_up = True
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
