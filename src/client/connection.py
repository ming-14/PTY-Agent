"""客户端连接层 —— 三路分流连接、消息往返与认证装配。

连接职责（ClientConnectionMixin）：
- connect_addr / _probe_port：按 CONNECT_MODE 决定连接目标
- _connect / _connect_token / _connect_basic / _connect_tls：三路分流
- _send_recv：一次完整请求/响应往返（含信封封装、凭证注入、插件变换链）
- _load_signer_and_providers：按 CONNECT_MODE 装配签名器与凭证提供者
"""

import socket
import ssl
import sys
import time
from typing import Optional

from ..auth.password import PasswordCredentialProvider
from ..auth.token import HmacMessageSigner, TokenCredentialProvider
from ..config.client import (
    BASIC_HOST,
    BASIC_PASSWORD,
    BASIC_PORT,
    CONNECT_MODE,
    CONNECT_TIMEOUT,
    KNOWN_HOSTS_FILE,
    PUBKEY_PRIVATE_KEY_PATH,
    TLS_HOST,
    TLS_PORT,
    TOFU_STRICT,
    TOKEN_HOST,
    TOKEN_PORT,
)
from ..ipc.shm import read_hmac_key
from ..protocol.envelope import request as _env_request, unwrap as _env_unwrap
from ..protocol.message import Message
from ..protocol.response import Response
from ..logging import get_logger
from . import presenter

_logger = get_logger("pty-client")


def _load_signer_and_providers():
    """按 CONNECT_MODE 装配签名器与凭证提供者

    连接模式决定认证方式（与 daemon 侧对应监听器匹配）：
    - "token":  Token + HMAC 对称，出站签请求 + 入站验响应（双向保护）
                （令牌与 HMAC 密钥经同机 SHM 分发）
    - "tls":    Ed25519 非对称单向，出站签请求，入站不验响应（响应裸传）
    - "basic":  BASIC_PASSWORD 非空时密码 + HMAC 双向（密码即密钥），空时无认证

    设置 Message 出/入站签名器，返回 providers 列表供调用方使用。

    幂等：若 Message 出站签名器已设置则直接返回 None（已装配过）。
    """
    if Message.get_outbound_signer() is not None:
        return None

    providers = []

    if CONNECT_MODE == "token":
        # HMAC 对称：出站签请求 + 入站验响应，复用同一实例。
        # daemon 刚启动（自动拉起/重启）时密钥可能尚未发布到 SHM，短重试
        # 消除该窗口（并发客户端同时触发自动启动时尤为明显）
        key = None
        for _ in range(3):
            key = read_hmac_key()
            if key is not None:
                break
            time.sleep(0.1)
        if key is None:
            _logger.warning("Token 连接已启用但无法从共享内存读取 HMAC 密钥")
        else:
            signer = HmacMessageSigner(key)
            Message.set_outbound_signer(signer)
            Message.set_inbound_verifier(signer)
            providers.append(TokenCredentialProvider())
            _logger.info("客户端连接方式: token (HMAC 双向)")

    elif CONNECT_MODE == "tls":
        # Ed25519 非对称单向：出站签请求，入站不验响应（无私钥验响应）
        # 惰性导入：keys/pubkey 顶层引入 cryptography，仅 tls 连接模式加载
        from ..auth.keys import PrivateKey
        from ..auth.pubkey import (
            Ed25519MessageSigner,
            PubkeyCredentialProvider,
        )

        try:
            private_key = PrivateKey.from_file(PUBKEY_PRIVATE_KEY_PATH)
        except (FileNotFoundError, PermissionError, ValueError) as e:
            _logger.error("加载 Ed25519 私钥失败 (%s): %s", PUBKEY_PRIVATE_KEY_PATH, e)
            raise
        Message.set_outbound_signer(Ed25519MessageSigner(private_key=private_key))
        Message.set_inbound_verifier(None)
        providers.append(PubkeyCredentialProvider(private_key))
        _logger.info("客户端连接方式: tls (Ed25519 单向)")

    elif BASIC_PASSWORD:
        # 密码即 HMAC 密钥：对称双向签名 + 密码身份校验（与 daemon 侧 BASIC_PASSWORD 一致）
        signer = HmacMessageSigner(BASIC_PASSWORD.encode("utf-8"))
        Message.set_outbound_signer(signer)
        Message.set_inbound_verifier(signer)
        providers.append(PasswordCredentialProvider(BASIC_PASSWORD))
        _logger.info("客户端连接方式: basic (密码 + HMAC 双向)")

    else:
        # basic 空密码，无认证
        Message.set_outbound_signer(None)
        Message.set_inbound_verifier(None)
        _logger.warning("客户端连接方式: basic (无认证)")

    return providers


class ClientConnectionMixin:
    """连接与消息往返职责（connect_addr / _connect* / _send_recv）"""

    def connect_addr(self) -> tuple:
        """按 CONNECT_MODE 返回连接目标地址 (host, port)"""
        if CONNECT_MODE == "tls":
            return TLS_HOST, TLS_PORT
        if CONNECT_MODE == "basic":
            return BASIC_HOST, BASIC_PORT
        return TOKEN_HOST, TOKEN_PORT

    def _probe_port(self) -> Optional[int]:
        """探测 daemon 端口

        token 模式：本地 SHM 发现（本机 daemon 是否运行）；
        basic/tls 模式：配置的目标端口（远程是否可达由连接探测）。
        """
        if CONNECT_MODE == "token":
            from .daemonctl import _find_daemon_port

            return _find_daemon_port()
        return self.connect_addr()[1]

    def _connect(self, autostart: bool = True) -> socket.socket:
        """连接守护进程（按 CONNECT_MODE 三路分流）

        - tls:   TLS 连接 + TOFU 验证（_connect_tls），远程跨机
        - basic: 直接明文连接（_connect_basic），密码认证或空密码无认证
        - token: 明文连接 + SHM 发现（_connect_token），本机同机

        Args:
            autostart: token 模式下守护进程未运行时是否自动启动。

        Returns:
            已连接的 socket（明文）或 SSLSocket（TLS）。
        """
        if CONNECT_MODE == "tls":
            return self._connect_tls()
        if CONNECT_MODE == "basic":
            return self._connect_basic()
        return self._connect_token(autostart)

    def _connect_token(self, autostart: bool = True) -> socket.socket:
        """连接本机 token 监听器（SHM 发现 + token/HMAC 认证）

        通过共享内存发现 daemon 存活与端口，创建明文 socket 连接，
        装配签名器与凭证提供者。autostart=True 时守护进程未运行则自动启动。
        """
        from .daemonctl import _find_daemon_port

        port = _find_daemon_port()
        if port is None:
            if not autostart:
                presenter.print_response(Response.error("daemon not running"))
                sys.exit(1)
            _logger.info("守护进程未运行，自动启动")
            from .daemonctl import start_daemon

            start_daemon()
            port = _find_daemon_port()
            if port is None:
                _logger.error("启动守护进程失败")
                presenter.print_response(
                    Response.error(
                        "failed to start daemon (port not found in shm after start_daemon returned)"
                    )
                )
                sys.exit(1)

        last_err = None
        for attempt in range(5):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(CONNECT_TIMEOUT)
                sock.connect((TOKEN_HOST, port))
                _logger.info("已连接守护进程 %s:%s", TOKEN_HOST, port)
                # 懒装配签名器与凭证提供者（Token + HMAC 对称认证）
                # 幂等：已装配时返回 None，跳过 provider 重建
                providers = _load_signer_and_providers()
                if providers is not None:
                    self._credential_provider = providers[0] if providers else None
                return sock
            except ConnectionRefusedError as e:
                last_err = e
                _logger.debug(
                    "_connect_token: attempt %d refused, retrying...", attempt + 1
                )
                time.sleep(0.2)
                new_port = _find_daemon_port()
                if new_port is None:
                    if not autostart:
                        presenter.print_response(Response.error("daemon not running"))
                        sys.exit(1)
                    _logger.info("守护进程已崩溃，自动重启")
                    from .daemonctl import start_daemon

                    start_daemon()
                    new_port = _find_daemon_port()
                    if new_port is None:
                        _logger.error("重启守护进程失败")
                        presenter.print_response(Response.error("failed to restart daemon"))
                        sys.exit(1)
                if new_port != port:
                    port = new_port
        _logger.error("连接守护进程失败: %s", last_err)
        presenter.print_response(Response.error(f"failed to connect to daemon: {last_err}"))
        sys.exit(1)

    def _connect_basic(self) -> socket.socket:
        """直接连接明文监听器（CONNECT_MODE=basic）

        密码认证与否取决于 BASIC_PASSWORD：非空时密码 + HMAC 双向签名，
        空时无认证；不做 SHM 发现与自动启动；常用于内网/局域网直连。
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect((BASIC_HOST, BASIC_PORT))
            _logger.info("已连接 basic 监听器 %s:%s", BASIC_HOST, BASIC_PORT)
            providers = _load_signer_and_providers()
            if providers is not None:
                self._credential_provider = providers[0] if providers else None
            return sock
        except ConnectionRefusedError as e:
            _logger.error("连接 basic 监听器失败: %s", e)
            presenter.print_response(Response.error(f"failed to connect to daemon: {e}"))
            sys.exit(1)

    def _connect_tls(self) -> ssl.SSLSocket:
        """TLS 连接守护进程（CONNECT_MODE=tls）

        1. 从配置获取远程 daemon 地址（TLS_HOST:TLS_PORT）
        2. KnownHosts 加载 TOFU 信任存储
        3. TLSClient 建立 TLS 连接 + TOFU 证书验证
        4. 装配 Ed25519 签名器与凭证提供者
        """
        # 惰性导入：known_hosts 无 crypto 依赖，但随 tls 分支一并懒加载；
        # TLSClient 惰性导入避免 tls 分支无谓加载 crypto 依赖
        from ..auth.tls.known_hosts import KnownHosts
        from .tls_client import TLSClient

        known_hosts = KnownHosts(KNOWN_HOSTS_FILE)
        tls_client = TLSClient(TLS_HOST, TLS_PORT, known_hosts, TOFU_STRICT)
        ssl_sock = tls_client.connect()
        _logger.info("已连接远程守护进程 (TLS) %s:%d", TLS_HOST, TLS_PORT)

        # 装配 Ed25519 签名器（幂等：已装配时返回 None）
        providers = _load_signer_and_providers()
        if providers is not None:
            self._credential_provider = providers[0] if providers else None
        return ssl_sock

    def _send_recv(
        self,
        msg: dict,
        *,
        autostart: bool = True,
        output_path: Optional[str] = None,
    ) -> dict:
        sock = self._connect(autostart=autostart)
        # CLI 插件 before_request 链：请求发送前变换
        if self._cli_plugins is not None:
            if output_path is not None:
                self._cli_plugins.set_output_path(output_path)
            msg = self._cli_plugins.before_request(msg.get("type", ""), msg)
        # 信封封装：分组负载（op/condition/output/io）+ 信封元数据；
        # 认证凭证（token/password/pubkey_fp）在随后 enrich 时注入到信封顶层，
        # 与请求一并参与签名，保证业务内容与身份同时受保护
        req_timeout = msg.get("timeout")
        msg = _env_request(msg.get("type", ""), msg)
        # 凭证提供者可能为 None（认证全关模式），条件调用
        if self._credential_provider is not None:
            self._credential_provider.enrich(msg)
        msg_type = msg.get("type", "?")
        _logger.debug("_send_recv: type=%s id=%s", msg_type, msg.get("id", ""))
        try:
            if req_timeout is not None:
                sock.settimeout(float(req_timeout) + 30.0)
            Message.send(sock, msg)
            resp = Message.recv(sock)
            if resp is None:
                _logger.warning("_send_recv: type=%s no response", msg_type)
                resp = Response.error("no response")
            else:
                # 拆响应信封 → 扁平 body（内部业务层沿原语义消费）
                _, resp, _ = _env_unwrap(resp)
            # CLI 插件 transform_response 链：响应收到后、业务后处理前变换
            if self._cli_plugins is not None:
                resp = self._cli_plugins.transform_response(msg_type, resp)
            return resp
        except ConnectionError as e:
            _logger.warning("_send_recv: type=%s connection error: %s", msg_type, e)
            return Response.error("connection closed")
        finally:
            try:
                sock.close()
            except OSError:
                pass