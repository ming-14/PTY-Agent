"""TCP 服务器 — DaemonServer

后台守护进程的 TCP 主循环，负责编排多个 Listener 并管理生命周期。
绑定端口后通过共享内存发布 PID+端口号。

Phase 3 重构：从单端口内联 accept 循环改为多 Listener 架构，
认证配置封装为 AuthContext 供每个 Listener 独立持有。

Phase 4 扩展：双端口架构 — 明文 Listener（token 认证，SHM 同机发现）
+ TLS Listener（pubkey 认证，跨机访问，自签证书自动生成）。
"""

import os
import mmap
import signal
import logging
import threading
import time
from typing import Optional

from ..config.common import DAEMON_HOST, IS_WINDOWS
from ..config.daemon import (
    DEFAULT_DAEMON_PORT,
    AUTH_TOKEN_ROTATE_INTERVAL,
    ENABLE_TOKEN_AUTH,
    ENABLE_PUBKEY_AUTH,
    PUBKEY_AUTHORIZED_KEYS,
    PUBKEY_LISTEN_HOST,
    PUBKEY_LISTEN_PORT,
    TLS_CERT_DIR,
    TLS_CERT_FILE,
    TLS_KEY_FILE,
    TLS_CERT_VALIDITY_DAYS,
    TLS_CERT_SUBJECT_CN,
    ENABLE_WEB,
    WEB_HOST,
    WEB_PORT,
)
from ..ipc.shm import (
    write_daemon_info_to_shm,
    read_daemon_info_from_shm,
    generate_auth_token,
    write_auth_token,
    write_hmac_key,
)
from ..auth.token import TokenAuthenticator
from ..auth.token import HmacMessageSigner
from ..auth.pubkey import Ed25519MessageSigner
from ..auth.pubkey import PubkeyAuthenticator
from ..auth.keys import load_authorized_keys
from ..auth.context import AuthContext
from ..auth.tls.cert_manager import CertificateManager
from ..session.manager import SessionManager
from .handler import RequestHandler
from .listener import Listener
from ..web.server import WebServer

_logger = logging.getLogger("pty-daemon")


class DaemonServer:
    """后台守护进程 TCP 服务器

    负责：
    - 编排多个 Listener（Phase 4: 明文 + TLS 双端口）
    - 绑定成功后通过共享内存发布 PID+端口号（仅明文端口）
    - 信号注册与处理
    - 资源清理（会话停止、共享内存释放、Listener 停止）
    - 认证令牌定时轮换（仅 token 认证模式）

    Attributes:
        host: 监听地址（明文端口）。
        port: 监听端口（明文端口）。
    """

    def __init__(self, host: str = DAEMON_HOST, port: int = DEFAULT_DAEMON_PORT):
        self.host = host
        self.port = port
        self.manager = SessionManager()
        self._listeners: list = []
        self._shutdown_event = threading.Event()
        self._running = False
        self._cleaned_up = False
        self._port_shm: Optional[mmap.mmap] = None
        self._auth_shm: Optional[mmap.mmap] = None
        self._hmac_shm: Optional[mmap.mmap] = None
        # _auth_token 始终生成：ENABLE_TOKEN_AUTH=true 时用于认证与 SHM 发布；
        # ENABLE_TOKEN_AUTH=false 时仅生成不发布
        self._auth_token: str = generate_auth_token()
        # HMAC 密钥由 _build_token_auth_context 生成，供 run() 写入 SHM
        self._hmac_key: Optional[bytes] = None
        # Token 认证器引用，仅 ENABLE_TOKEN_AUTH=true 时创建，供 _rotate_token 调用
        self._token_authenticator: Optional[TokenAuthenticator] = None
        self._rotate_timer: Optional[threading.Timer] = None
        self._last_health_check: float = 0.0
        self._start_time: float = time.time()
        # SHM 是否已发布（TLS-only 模式下为 False，跳过健康检查）
        self._shm_published: bool = False

    def _schedule_rotate(self):
        """安排下一次令牌轮换"""
        self._rotate_timer = threading.Timer(
            AUTH_TOKEN_ROTATE_INTERVAL, self._rotate_token,
        )
        self._rotate_timer.daemon = True
        self._rotate_timer.start()

    def _rotate_token(self):
        """生成新令牌并推送到共享内存和 TokenAuthenticator

        仅 ENABLE_TOKEN_AUTH=true 时由 _schedule_timer 触发。
        防御性检查 _token_authenticator：若运行时被清空则跳过轮换与下次调度。
        """
        if not self._token_authenticator:
            _logger.debug("_rotate_token: token 认证未启用，跳过轮换")
            return
        old_token = self._auth_token
        self._auth_token = generate_auth_token()
        try:
            self._auth_shm.close()
        except Exception:
            pass
        self._auth_shm = write_auth_token(self._auth_token)
        self._token_authenticator.rotate_token(self._auth_token, old_token)
        _logger.info("认证令牌已轮换")
        self._schedule_rotate()

    def _build_token_auth_context(self) -> AuthContext:
        """构建 Token 认证上下文（明文 Listener 使用）

        Token + HMAC 对称认证：HMAC 密钥生成但不写入 SHM（由 run() 负责写入）。
        HMAC 对称：daemon 既能签响应（出站）也能验请求（入站），复用同一实例。

        若 Token 认证未启用（且无其他认证方式），返回无认证上下文（本地调试模式）。

        Returns:
            AuthContext 封装出站签名器、入站验证器、认证器
        """
        if not ENABLE_TOKEN_AUTH:
            # 无认证模式（本地调试）：明文端口无认证
            _logger.warning(
                "认证已关闭（ENABLE_TOKEN_AUTH=false 且 ENABLE_PUBKEY_AUTH=false），"
                "任意请求均不校验，仅适用于本地调试"
            )
            return AuthContext(None, None, None)

        # Token + HMAC 对称认证：生成 HMAC 密钥
        hmac_key = os.urandom(32)
        hmac_signer = HmacMessageSigner(hmac_key)
        self._hmac_key = hmac_key  # 保存供 run() 写入 SHM
        self._token_authenticator = TokenAuthenticator(self._auth_token)
        _logger.info("Token + HMAC 认证已启用（明文端口）")
        return AuthContext(hmac_signer, hmac_signer, self._token_authenticator)

    def _build_pubkey_auth_context(self) -> AuthContext:
        """构建公私钥认证上下文（TLS Listener 使用）

        Ed25519 非对称单向认证：daemon 仅验请求（入站），不签响应（无私钥）。
        fail-closed：白名单为空时所有公私钥请求将被拒绝。

        Returns:
            AuthContext 封装出站签名器（None）、入站验证器、认证器
        """
        authorized_keys = load_authorized_keys(PUBKEY_AUTHORIZED_KEYS)
        if not authorized_keys:
            _logger.warning(
                "公私钥认证已启用但 authorized_keys 为空 (%s)，"
                "所有公私钥认证请求将被拒绝（fail-closed）",
                PUBKEY_AUTHORIZED_KEYS,
            )
        verifier = Ed25519MessageSigner(authorized_keys=authorized_keys)
        authenticator = PubkeyAuthenticator(authorized_keys)
        _logger.info(
            "Ed25519 公私钥认证已启用（TLS 端口），authorized_keys 加载 %d 个公钥",
            len(authorized_keys),
        )
        return AuthContext(None, verifier, authenticator)

    def _create_handler(self, auth_context: AuthContext) -> RequestHandler:
        """handler_factory — 接收 AuthContext，返回 RequestHandler 实例

        Listener.start() 调用此方法创建 handler，之后所有连接复用同一 handler。
        """
        return RequestHandler(self.manager, auth_context, server=self)

    def run(self):
        """启动服务器主循环

        Phase 4 双端口架构：
        - 明文 Listener（token 认证 / 无认证，SHM 同机发现）
        - TLS Listener（pubkey 认证，跨机访问，不发布 SHM）

        两者都开时双端口同时工作；仅开 pubkey 为 TLS-only 模式（无明文端口）。

        流程：
        1. 构建认证上下文 + 创建/绑定 Listener（plain + TLS）
        2. SHM 单实例检查
        3. 写入认证令牌 + HMAC 密钥到 SHM（仅 token 认证）
        4. 发布守护进程信息到 SHM（仅明文 Listener）
        5. 启动所有 Listener（accept 线程）
        6. 启动 Web 服务器、令牌轮换、信号处理
        7. 主线程阻塞等待关闭信号
        """
        # 判断需要哪些 Listener
        need_plain = ENABLE_TOKEN_AUTH or not ENABLE_PUBKEY_AUTH
        need_tls = ENABLE_PUBKEY_AUTH

        listeners_to_start: list = []

        try:
            # 1. 构建认证上下文 + 创建/绑定 Listener
            if need_plain:
                token_ctx = self._build_token_auth_context()
                plain_listener = Listener(
                    host=self.host, port=self.port,
                    transport="plain", auth_context=token_ctx,
                    publish_shm=True,
                )
                plain_listener.bind()
                listeners_to_start.append(plain_listener)

            if need_tls:
                pubkey_ctx = self._build_pubkey_auth_context()
                # 生成/加载 TLS 证书（首次启动自动生成自签证书）
                cert_mgr = CertificateManager(
                    cert_dir=TLS_CERT_DIR,
                    cert_file=TLS_CERT_FILE,
                    key_file=TLS_KEY_FILE,
                    validity_days=TLS_CERT_VALIDITY_DAYS,
                    subject_cn=TLS_CERT_SUBJECT_CN,
                )
                cert_mgr.ensure_certificate()
                ssl_ctx = cert_mgr.create_server_ssl_context()

                tls_listener = Listener(
                    host=PUBKEY_LISTEN_HOST, port=PUBKEY_LISTEN_PORT,
                    transport="tls", auth_context=pubkey_ctx,
                    ssl_context=ssl_ctx,
                    publish_shm=False,
                )
                tls_listener.bind()
                listeners_to_start.append(tls_listener)

        except OSError as e:
            if e.errno == 98 or "EADDRINUSE" in str(e) or "10048" in str(e):
                _logger.error(
                    "端口已被占用（可能已有其他守护进程在运行），"
                    "请检查或指定其他端口"
                )
            else:
                _logger.error("绑定端口失败: %s", e)
            for l in listeners_to_start:
                l.stop()
            raise

        # 2. SHM 单实例检查
        existing = read_daemon_info_from_shm()
        if existing is not None:
            existing_pid, existing_port = existing
            from .lifecycle import _pid_exists, _ping_daemon
            if _pid_exists(existing_pid) and _ping_daemon(existing_port):
                _logger.error(
                    "守护进程已在运行 (PID:%d 端口:%d)，拒绝覆盖共享内存",
                    existing_pid, existing_port,
                )
                for l in listeners_to_start:
                    l.stop()
                raise RuntimeError(
                    f"守护进程已在运行 (PID:{existing_pid} 端口:{existing_port})"
                )

        # 3. 写入认证令牌 + HMAC 密钥到 SHM（仅 token 认证模式）
        #    客户端通过 SHM 读取 token + HMAC 密钥进行对称认证
        if ENABLE_TOKEN_AUTH:
            try:
                self._auth_shm = write_auth_token(self._auth_token)
                if IS_WINDOWS:
                    _logger.info("认证令牌已发布")
            except Exception as e:
                _logger.error("写入认证令牌失败: %s", e)
                for l in listeners_to_start:
                    l.stop()
                raise

            if self._hmac_key is not None:
                try:
                    self._hmac_shm = write_hmac_key(self._hmac_key)
                except Exception as e:
                    _logger.error("写入 HMAC 密钥失败: %s", e)
                    for l in listeners_to_start:
                        l.stop()
                    raise

        # 4. 发布守护进程信息到 SHM（仅明文 Listener，用于同机发现）
        if need_plain:
            actual_port = listeners_to_start[0].port  # 明文 Listener 实际端口
            try:
                self._port_shm = write_daemon_info_to_shm(os.getpid(), actual_port)
                if IS_WINDOWS:
                    _logger.info("共享内存已发布 PID:%d 端口:%d", os.getpid(), actual_port)
            except Exception as e:
                _logger.error("发布守护进程信息失败: %s", e)
                for l in listeners_to_start:
                    l.stop()
                raise

            self.port = actual_port
            self._my_shm_signature = f"{os.getpid()}:{actual_port}"
            self._shm_published = True

        self._running = True

        # 5. 启动所有 Listener（开始 accept 线程）
        self._listeners = listeners_to_start
        for listener in self._listeners:
            listener.start(self._create_handler)
        _logger.info("守护进程启动完成，共 %d 个 Listener", len(self._listeners))

        # 6. 启动 Web 服务器（ENABLE_WEB=False 时跳过，同时禁用 VNC 和 FastScreen）
        self._web_server = None
        if ENABLE_WEB:
            self._web_server = WebServer(
                self.manager, host=WEB_HOST, port=WEB_PORT,
            )
            self._web_server.start_background()
            web_url = f"http://{WEB_HOST}:{WEB_PORT}/"
            _logger.info("Web 服务器已启动，可通过 %s 访问", web_url)
        else:
            # Web 关闭时 VNC 和 FastScreen 无访问入口，强制禁用
            from ..config import daemon as _daemon_cfg
            if _daemon_cfg.ENABLE_VNC:
                _daemon_cfg.ENABLE_VNC = False
                _logger.info("ENABLE_WEB=False，自动禁用 VNC")
            if _daemon_cfg.ENABLE_FASTSCREEN:
                _daemon_cfg.ENABLE_FASTSCREEN = False
                _logger.info("ENABLE_WEB=False，自动禁用 FastScreen")
            _logger.info("Web 服务器已禁用 (ENABLE_WEB=False)")

        # 标记 ended 会话为 history
        if self.manager._history_store:
            try:
                n = self.manager._history_store.mark_all_ended_as_history()
                if n > 0:
                    _logger.info("启动时将 %d 个 ended 会话标记为 history", n)
            except Exception:
                _logger.warning("标记 ended 会话为 history 时异常", exc_info=True)

        # 仅在 Token 认证启用时启动令牌轮换（pubkey 模式无对称令牌需轮换）
        if ENABLE_TOKEN_AUTH:
            self._schedule_rotate()

        # 信号处理
        def _signal_handler(signum, frame):
            _logger.info("收到信号 %s，关闭守护进程...", signum)
            self._shutdown_event.set()
        signal.signal(signal.SIGTERM, _signal_handler)
        if not IS_WINDOWS:
            signal.signal(signal.SIGHUP, _signal_handler)

        # 7. 主线程阻塞等待关闭信号，定期健康检查
        try:
            while not self._shutdown_event.wait(30.0):
                self._periodic_health_check()
        except Exception:
            _logger.exception("服务器主循环异常")
        finally:
            self._cleanup()

    def stop(self):
        """停止服务器"""
        self._shutdown_event.set()
        self._cleanup()

    def _verify_shm(self) -> bool:
        """检查共享内存是否仍属于当前守护进程。

        如果共享内存被另一个实例覆盖，返回 False，调用方应退出。
        仅在启动时调用一次，不在 accept 循环中反复检查。
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

    def _periodic_health_check(self):
        """定期检查共享内存与实际端口是否一致（每 30 秒一次）

        TLS-only 模式下（无 SHM 发布）跳过此检查。
        """
        if not self._shm_published:
            return
        now = time.monotonic()
        if now - self._last_health_check < 30.0:
            return
        self._last_health_check = now
        try:
            info = read_daemon_info_from_shm()
            expected_pid = os.getpid()
            expected_port = self.port
            if info is None:
                _logger.error(
                    "[HEALTH] 共享内存丢失! 预期 PID:%d 端口:%d，实际共享内存为空。"
                    " 客户端将无法发现此守护进程。",
                    expected_pid, expected_port,
                )
                return
            actual_pid, actual_port = info
            if actual_pid != expected_pid or actual_port != expected_port:
                _logger.error(
                    "[HEALTH] 共享内存不一致! 预期 PID:%d 端口:%d，"
                    "实际 PID:%d 端口:%d",
                    expected_pid, expected_port, actual_pid, actual_port,
                )
        except Exception as e:
            _logger.error("[HEALTH] 共享内存健康检查异常: %s", e)

    def _cleanup(self):
        """清理资源：停止所有 Listener + 会话 + 释放共享内存"""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._running = False
        self._shutdown_event.set()  # 唤醒主线程

        # 停止所有 Listener（关闭 socket → accept 线程退出）
        for listener in self._listeners:
            listener.stop()
        self._listeners.clear()

        if self._rotate_timer:
            self._rotate_timer.cancel()
            self._rotate_timer = None
        if hasattr(self, '_web_server') and self._web_server:
            try:
                self._web_server.stop()
            except Exception:
                pass
        self.manager.stop_all()
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
        try:
            if getattr(self, '_hmac_shm', None):
                self._hmac_shm.close()
        except (ValueError, OSError):
            pass
        self._hmac_shm = None
        _logger.info("守护进程已停止")
