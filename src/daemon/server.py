"""TCP 服务器 — DaemonServer

后台守护进程的 TCP 主循环，负责编排多个 Listener 并管理生命周期。
监听位置完全由 daemon.toml [listener] 段控制，支持三监听器同开或只开一个。

多 Listener 架构：认证配置封装为 AuthContext，每个 Listener 独立持有。
- plain Listener（明文，无认证，PLAIN_HOST:PLAIN_PORT）
- token Listener（Token + HMAC 认证，TOKEN_HOST:TOKEN_PORT，同机认证凭据经 SHM 分发）
- tls Listener（TLS + pubkey 认证，TLS_HOST:TLS_PORT，自签证书自动生成）
"""

import logging
import mmap
import os
import signal
import threading
import time
from typing import Optional

from ..auth.context import AuthContext
from ..auth.keys import load_authorized_keys
from ..auth.pubkey import Ed25519MessageSigner, PubkeyAuthenticator
from ..auth.tls.cert_manager import CertificateManager
from ..auth.token import HmacMessageSigner, TokenAuthenticator
from ..config.daemon import (
    AUTH_TOKEN_ROTATE_INTERVAL,
    ENABLE_WEB,
    IS_WINDOWS,
    PLAIN_ENABLED,
    PLAIN_HOST,
    PLAIN_PORT,
    PUBKEY_AUTHORIZED_KEYS,
    TLS_CERT_DIR,
    TLS_CERT_FILE,
    TLS_CERT_SUBJECT_CN,
    TLS_CERT_VALIDITY_DAYS,
    TLS_ENABLED,
    TLS_HOST,
    TLS_KEY_FILE,
    TLS_PORT,
    TOKEN_ENABLED,
    TOKEN_HOST,
    TOKEN_PORT,
    WEB_HOST,
    WEB_PASSWORD_HASH,
    WEB_PORT,
)
from ..config.plugins import ENABLED as PLUGINS_ENABLED
from ..config.plugins import PLUGIN_PATHS
from ..ipc.shm import (
    generate_auth_token,
    write_auth_token,
    write_hmac_key,
)
from ..plugins.registry import PluginRegistry
from ..session.manager import SessionManager
from ..web.server import WebServer
from .handler import RequestHandler
from .listener import Listener

_logger = logging.getLogger("pty-daemon")


class DaemonServer:
    """后台守护进程 TCP 服务器

    负责：
    - 编排多个 Listener（plain / token / tls，按 [listener] 段独立启停）
    - 信号注册与处理
    - 资源清理（会话停止、共享内存释放、Listener 停止）
    - 认证令牌定时轮换（仅 token 监听器启用时）

    Attributes:
        listeners_config: [listener] 段三段配置（enabled/host/port）
    """

    def __init__(self):
        # 监听器配置快照：三段各自独立（enabled/host/port），同开或只开一个
        self.listeners_config = {
            "plain": (PLAIN_ENABLED, PLAIN_HOST, PLAIN_PORT),
            "token": (TOKEN_ENABLED, TOKEN_HOST, TOKEN_PORT),
            "tls": (TLS_ENABLED, TLS_HOST, TLS_PORT),
        }
        # 插件注册表：守护进程启动时扫描加载一次（enabled=false 或加载异常时禁用）
        self.manager = SessionManager(plugin_registry=self._create_plugin_registry())
        self._listeners: list = []
        self._shutdown_event = threading.Event()
        self._running = False
        self._cleaned_up = False
        self._auth_shm: Optional[mmap.mmap] = None
        self._hmac_shm: Optional[mmap.mmap] = None
        # _auth_token 始终生成：token 监听器启用时用于认证与 SHM 发布；否则仅生成不发布
        self._auth_token: str = generate_auth_token()
        # HMAC 密钥由 _build_token_auth_context 生成，供 run() 写入 SHM
        self._hmac_key: Optional[bytes] = None
        # Token 认证器引用，仅 token 监听器启用时创建，供 _rotate_token 调用
        self._token_authenticator: Optional[TokenAuthenticator] = None
        self._rotate_timer: Optional[threading.Timer] = None
        self._start_time: float = time.time()

    def _create_plugin_registry(self) -> Optional[PluginRegistry]:
        """创建进程级插件注册表；禁用或初始化异常时返回 None（插件系统关闭）"""
        if not PLUGINS_ENABLED:
            _logger.info("插件系统已禁用 (plugins.enabled=false)")
            return None
        try:
            registry = PluginRegistry(PLUGIN_PATHS)
            _logger.info("插件注册表初始化完成，位置: %s", PLUGIN_PATHS)
            return registry
        except Exception:
            _logger.exception("插件注册表初始化失败，插件系统禁用")
            return None

    def _schedule_rotate(self):
        """安排下一次令牌轮换"""
        self._rotate_timer = threading.Timer(
            AUTH_TOKEN_ROTATE_INTERVAL,
            self._rotate_token,
        )
        self._rotate_timer.daemon = True
        self._rotate_timer.start()

    def _rotate_token(self):
        """生成新令牌并推送到共享内存和 TokenAuthenticator

        仅 token 监听器启用时由 _schedule_timer 触发。
        防御性检查 _token_authenticator：若运行时被清空则跳过轮换与下次调度。
        """
        if not self._token_authenticator:
            _logger.debug("_rotate_token: token 监听器未启用，跳过轮换")
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

    def _build_plain_auth_context(self) -> AuthContext:
        """构建无认证上下文（plain Listener 使用）

        明文无认证监听器：任意请求均不校验，仅适用于受信任网络/本地调试。
        """
        _logger.warning(
            "plain 监听器启用（无认证），任意请求均不校验，仅适用于受信任网络"
        )
        return AuthContext(None, None, None)

    def _build_token_auth_context(self) -> AuthContext:
        """构建 Token 认证上下文（token Listener 使用）

        Token + HMAC 对称认证：HMAC 密钥生成但不写入 SHM（由 run() 负责写入）。
        HMAC 对称：daemon 既能签响应（出站）也能验请求（入站），复用同一实例。

        Returns:
            AuthContext 封装出站签名器、入站验证器、认证器
        """
        # Token + HMAC 对称认证：生成 HMAC 密钥
        hmac_key = os.urandom(32)
        hmac_signer = HmacMessageSigner(hmac_key)
        self._hmac_key = hmac_key  # 保存供 run() 写入 SHM
        self._token_authenticator = TokenAuthenticator(self._auth_token)
        _logger.info("Token + HMAC 认证已启用（token 监听器）")
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

    def _log_listeners(self):
        """输出各监听器状态日志（enabled 监听位置 / disabled 跳过）"""
        for name, (enabled, host, port) in self.listeners_config.items():
            if enabled:
                _logger.info("Listener [%s] 已启用: %s:%d", name, host, port)
            else:
                _logger.info("Listener [%s] 未启用（[listener] 段 disabled）", name)

    def run(self):
        """启动服务器主循环

        三监听器架构（[listener] 段独立配置，同开或只开一个）：
        - plain Listener（无认证，明文）
        - token Listener（Token + HMAC 认证，明文，SHM 分发凭据）
        - tls Listener（pubkey 认证，TLS，跨机访问）

        流程：
        1. 按 [listener] 段逐个构建认证上下文 + 创建/绑定 Listener
        2. 写入认证令牌 + HMAC 密钥到 SHM（仅 token 监听器启用时）
        3. 启动所有 Listener（accept 线程）
        4. 启动 Web 服务器、令牌轮换、信号处理
        5. 主线程阻塞等待关闭信号
        """
        listeners_to_start: list = []

        try:
            # 1. 按 [listener] 段逐个构建认证上下文 + 创建/绑定 Listener
            #    每段独立判断 enabled；同开或只开一个（配置见 self.listeners_config）
            plain_enabled, plain_host, plain_port = self.listeners_config["plain"]
            token_enabled, token_host, token_port = self.listeners_config["token"]
            tls_enabled, tls_host, tls_port = self.listeners_config["tls"]

            if plain_enabled:
                plain_ctx = self._build_plain_auth_context()
                plain_listener = Listener(
                    host=plain_host,
                    port=plain_port,
                    transport="plain",
                    auth_context=plain_ctx,
                )
                plain_listener.bind()
                listeners_to_start.append(plain_listener)

            if token_enabled:
                token_ctx = self._build_token_auth_context()
                token_listener = Listener(
                    host=token_host,
                    port=token_port,
                    transport="plain",
                    auth_context=token_ctx,
                )
                token_listener.bind()
                listeners_to_start.append(token_listener)

            if tls_enabled:
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
                    host=tls_host,
                    port=tls_port,
                    transport="tls",
                    auth_context=pubkey_ctx,
                    ssl_context=ssl_ctx,
                )
                tls_listener.bind()
                listeners_to_start.append(tls_listener)

        except OSError as e:
            if e.errno == 98 or "EADDRINUSE" in str(e) or "10048" in str(e):
                _logger.error(
                    "端口已被占用（可能已有其他守护进程在运行），请检查或指定其他端口"
                )
            else:
                _logger.error("绑定端口失败: %s", e)
            for l in listeners_to_start:
                l.stop()
            raise

        # 2. 写入认证令牌 + HMAC 密钥到 SHM（仅 token 监听器启用时）
        #    客户端通过 SHM 读取 token + HMAC 密钥进行对称认证
        token_enabled = self.listeners_config["token"][0]
        if token_enabled:
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

        self._running = True

        # 3. 启动所有 Listener（开始 accept 线程）
        self._listeners = listeners_to_start
        for listener in self._listeners:
            listener.start(self._create_handler)
        self._log_listeners()
        _logger.info("守护进程启动完成，共 %d 个 Listener", len(self._listeners))

        # 4. 启动 Web 服务器（ENABLE_WEB=False 时跳过，同时禁用 VNC 和 FastScreen）
        self._web_server = None
        if ENABLE_WEB:
            self._web_server = WebServer(
                self.manager,
                host=WEB_HOST,
                port=WEB_PORT,
                password_hash=WEB_PASSWORD_HASH,
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

        # 仅在 token 监听器启用时启动令牌轮换（plain/tls 无对称令牌需轮换）
        if self.listeners_config["token"][0]:
            self._schedule_rotate()

        # 信号处理
        def _signal_handler(signum, frame):
            _logger.info("收到信号 %s，关闭守护进程...", signum)
            self._shutdown_event.set()

        signal.signal(signal.SIGTERM, _signal_handler)
        if not IS_WINDOWS:
            signal.signal(signal.SIGHUP, _signal_handler)

        # 5. 主线程阻塞等待关闭信号
        try:
            while not self._shutdown_event.wait(30.0):
                pass
        except Exception:
            _logger.exception("服务器主循环异常")
        finally:
            self._cleanup()

    def stop(self):
        """停止服务器"""
        self._shutdown_event.set()
        self._cleanup()

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
        if hasattr(self, "_web_server") and self._web_server:
            try:
                self._web_server.stop()
            except Exception:
                pass
        self.manager.stop_all()
        try:
            if self._auth_shm:
                self._auth_shm.close()
        except (ValueError, OSError):
            pass
        self._auth_shm = None
        try:
            if getattr(self, "_hmac_shm", None):
                self._hmac_shm.close()
        except (ValueError, OSError):
            pass
        self._hmac_shm = None
        _logger.info("守护进程已停止")
