"""TCP 传输层 — Client 类

封装与守护进程的 TCP 通信，向 CLI 入口提供简洁的命令接口。
支持自动启动守护进程、临时默认配置覆盖。
"""

import base64
import gzip
import json
import os
import shlex
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
    DEFAULT_TRIGGER_TIMEOUT,
    KNOWN_HOSTS_FILE,
    PUBKEY_PRIVATE_KEY_PATH,
    TLS_HOST,
    TLS_PORT,
    TOFU_STRICT,
    TOKEN_HOST,
    TOKEN_PORT,
)
from ..config.common import IS_WINDOWS
from ..daemonctl import TLSClient
from ..ipc.shm import read_hmac_key
from ..protocol.message import Message
from ..protocol.response import Response


def _decompress_screen_buffer(resp: dict):
    if "screenBufferZ" not in resp:
        return
    try:
        compressed = base64.b64decode(resp.pop("screenBufferZ"))
        raw = gzip.decompress(compressed)
        resp["screenBuffer"] = json.loads(raw)
        resp.pop("screenBufferMeta", None)
    except Exception as e:
        _logger.warning("解压 screenBufferZ 失败: %s", e)


from ..daemonctl import start_daemon, stop_daemon
from .config_manager import _DEFAULTS as _DEFAULTS_MAP
from .config_manager import ConfigManager
from .formatter import print_response
from .input import process_input
from ..logging import get_logger

_logger = get_logger("pty-client")

_SHELL_OPS = frozenset({"|", "||", "&", "&&", ";", ">", "<", ">>"})


def _has_shell_operators(cmd: str) -> bool:
    try:
        tokens = shlex.split(cmd, posix=not IS_WINDOWS)
    except ValueError:
        return False
    return any(t in _SHELL_OPS for t in tokens)


def _parse_iso_time(s: str) -> float:
    from datetime import datetime

    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt.timestamp()


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


class Client:
    """前端客户端，封装与守护进程的 TCP 通信

    连接方式由 client.toml [connection] 的 CONNECT_MODE 决定，
    与 daemon 侧 [listener] 对应监听器匹配：
    - "basic": 直接连接 BASIC_HOST:BASIC_PORT，密码认证（BASIC_PASSWORD 空则无认证，不自动启动 daemon）
    - "token": 连接本机 TOKEN_HOST:TOKEN_PORT，SHM 发现 + Token/HMAC 认证
              （daemon 未运行时自动启动，本机同机场景）
    - "tls":   连接 TLS_HOST:TLS_PORT，TLS 传输 + TOFU 证书验证 + Ed25519 认证
    """

    def __init__(
        self,
        config_overrides: Optional[dict] = None,
        cli_plugins=None,
    ):
        """初始化客户端

        Args:
            config_overrides: 配置覆盖字典。
            cli_plugins: CLI 插件宿主（CliPluginHost）；None 表示不启用。
        """
        self._config = ConfigManager(overrides=config_overrides)
        # 凭证提供者懒加载：首次 _connect 时由 _load_signer_and_providers() 装配
        # providers 只有 0 或 1 个：单 provider / None（basic 无认证）
        self._credential_provider = None
        self._cli_plugins = cli_plugins

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
        token 模式：本地 SHM 发现（本机 daemon 是否运行）；
        basic/tls 模式：配置的目标端口（远程是否可达由连接探测）。
        """
        if CONNECT_MODE == "token":
            from ..daemonctl import _find_daemon_port

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
        from ..daemonctl import _find_daemon_port

        port = _find_daemon_port()
        if port is None:
            if not autostart:
                print_response(Response.error("daemon not running"))
                sys.exit(1)
            _logger.info("守护进程未运行，自动启动")
            start_daemon()
            port = _find_daemon_port()
            if port is None:
                _logger.error("启动守护进程失败")
                print_response(
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
                        print_response(Response.error("daemon not running"))
                        sys.exit(1)
                    _logger.info("守护进程已崩溃，自动重启")
                    start_daemon()
                    new_port = _find_daemon_port()
                    if new_port is None:
                        _logger.error("重启守护进程失败")
                        print_response(Response.error("failed to restart daemon"))
                        sys.exit(1)
                if new_port != port:
                    port = new_port
        _logger.error("连接守护进程失败: %s", last_err)
        print_response(Response.error(f"failed to connect to daemon: {last_err}"))
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
            print_response(Response.error(f"failed to connect to daemon: {e}"))
            sys.exit(1)

    def _connect_tls(self) -> ssl.SSLSocket:
        """TLS 连接守护进程（CONNECT_MODE=tls）

        1. 从配置获取远程 daemon 地址（TLS_HOST:TLS_PORT）
        2. KnownHosts 加载 TOFU 信任存储
        3. TLSClient 建立 TLS 连接 + TOFU 证书验证
        4. 装配 Ed25519 签名器与凭证提供者
        """
        # 惰性导入：known_hosts 无 crypto 依赖，但随 tls 分支一并懒加载
        from ..auth.tls.known_hosts import KnownHosts

        known_hosts = KnownHosts(KNOWN_HOSTS_FILE)
        tls_client = TLSClient(TLS_HOST, TLS_PORT, known_hosts, TOFU_STRICT)
        ssl_sock = tls_client.connect()
        _logger.info("已连接远程守护进程 (TLS) %s:%d", TLS_HOST, TLS_PORT)

        # 装配 Ed25519 签名器（幂等：已装配时返回 None）
        providers = _load_signer_and_providers()
        if providers is not None:
            self._credential_provider = providers[0] if providers else None
        return ssl_sock

    def _apply_config_defaults(
        self,
        *,
        timeout: Optional[float] = None,
        keep_ansi: Optional[bool] = None,
        encoding: Optional[str] = None,
        newline: Optional[bool] = None,
        send_eol: Optional[str] = None,
    ) -> tuple:
        cfg = self._config.get_all()
        if timeout is None:
            timeout = cfg.get("timeout", DEFAULT_TRIGGER_TIMEOUT)
        if keep_ansi is None:
            keep_ansi = cfg.get("keep_ansi", False)
        if encoding is None:
            encoding = cfg.get("encoding")
        if newline is None:
            newline = cfg.get("newline", False)
        if send_eol is None:
            send_eol = cfg.get("send_eol", "\r")
        return timeout, keep_ansi, encoding, newline, send_eol

    def _get_client_defaults(self) -> dict:
        cfg = self._config.get_all()
        defaults = {}
        for key in (
            "timeout",
            "newline",
            "keep_ansi",
            "encoding",
            "debug",
            "send_eol",
            "response_format",
            "svg_compression_level",
        ):
            val = cfg.get(key)
            if val is not None and val != _DEFAULTS_MAP.get(key):
                defaults[key] = val
        return defaults

    def _merge_session_defaults(self, resp: dict):
        session_defaults = resp.get("sessionDefaults")
        if not session_defaults or not isinstance(session_defaults, dict):
            return
        for key, val in session_defaults.items():
            if self._config.get(key) is None or self._config.get(
                key
            ) == _DEFAULTS_MAP.get(key):
                try:
                    self._config.set(key, val)
                except (ValueError, KeyError):
                    pass

    def _maybe_save_encoding(self, encoding: Optional[str]):
        if encoding is not None and self._config.get("encoding") != encoding:
            self._config.set("encoding", encoding)

    def _route_plugins(self, msg: dict, plugins) -> None:
        """exec --plugin 按插件形态分流挂载：CLI 形态客户端挂钩，daemon 形态透传

        插件在类声明处用 kind 声明自己支持哪侧钩子：
        - kind=cli：客户端进程内执行，本次调用挂载钩子（CliPluginHost.activate），
          并经 msg["cliPlugins"] 记录到会话，后续 read/send/mouse 客户端自动挂钩
        - kind=session/process：daemon 侧挂载，经 msg["plugins"] 透传会话创建
        """
        if not plugins:
            return
        cli_plugins = self._cli_plugins
        cli_names = cli_plugins.names() if cli_plugins is not None else []
        cli_hooks = []
        daemon_names = []
        for name in plugins:
            if cli_plugins is not None and name in cli_names:
                cli_hooks.append(name)
            else:
                daemon_names.append(name)
        if cli_hooks:
            cli_plugins.activate(cli_hooks)
            msg["cliPlugins"] = cli_hooks
        if daemon_names:
            msg["plugins"] = daemon_names

    def _session_cli_plugins(self, session_id: str) -> list:
        """查询会话挂载的 CLI 插件名（exec 时经 cliPlugins 记录在 daemon 会话上）

        read/send/mouse 每次调用据此自动挂钩，无需再传 --plugin。
        会话不存在/已结束返回空列表（CLI 插件仅在会话存活时回调）。
        """
        if self._cli_plugins is None:
            return []
        resp = self._send_recv(
            {"type": "plugin", "action": "ls", "id": session_id},
            autostart=False,
        )
        if resp.get("type") == "error":
            return []
        mounted = resp.get("plugins") or []
        names = [p.get("name") for p in mounted if isinstance(p, dict)]
        return [n for n in names if n in self._cli_plugins.names()]

    def _activate_session_cli(self, session_id: str) -> None:
        """挂载会话上记录的 CLI 插件钩子（read/send/mouse 每次调用自动生效）"""
        if self._cli_plugins is None:
            return
        self._cli_plugins.activate(self._session_cli_plugins(session_id))

    @staticmethod
    def _handle_output(
        output_path: Optional[str], resp: dict, svg_compression_level: int = 1
    ):
        if not output_path:
            return
        if resp.get("type") == "error":
            _logger.warning("请求失败，跳过输出到 %s", output_path)
            return
        from .renderer import is_image_ext, render_to_file

        if is_image_ext(output_path) and not resp.get("screenBuffer"):
            print_response(
                Response.error(
                    f"Image output requires a screen buffer (got --output {output_path})"
                )
            )
            return
        err = render_to_file(
            output_path, resp, svg_compression_level=svg_compression_level
        )
        if err:
            print_response(Response.error(err))

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
        # 凭证提供者可能为 None（认证全关模式），条件调用
        if self._credential_provider is not None:
            self._credential_provider.enrich(msg)
        msg_type = msg.get("type", "?")
        _logger.debug("_send_recv: type=%s id=%s", msg_type, msg.get("id", ""))
        try:
            req_timeout = msg.get("timeout")
            if req_timeout is not None:
                sock.settimeout(float(req_timeout) + 30.0)
            Message.send(sock, msg)
            resp = Message.recv(sock)
            if resp is None:
                _logger.warning("_send_recv: type=%s no response", msg_type)
                resp = Response.error("no response")
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

    def cmd_start(self):
        _logger.info("cmd_start")
        already_running = start_daemon()
        if already_running:
            from ..daemonctl import is_running

            if is_running():
                resp = self._send_recv({"type": "list"}, autostart=False)
                print_response(resp)

    def cmd_stop(self, force: bool = False):
        _logger.info("cmd_stop force=%s", force)
        stop_daemon(force=force)

    def cmd_status(self):
        _logger.info("cmd_status")
        port = self._probe_port()
        if port is None:
            print_response({"type": "status", "running": False})
            return
        pid = None
        if CONNECT_MODE == "token":
            from ..daemonctl import _find_daemon_pid

            pid = _find_daemon_pid()
        try:
            resp = self._send_recv({"type": "status"}, autostart=False)
            print_response(resp)
        except SystemExit:
            print_response({"type": "status", "running": False})
        except Exception as e:
            print_response(
                {
                    "type": "status",
                    "running": True,
                    "pid": pid,
                    "port": port,
                    "message": str(e),
                }
            )

    def cmd_exec(
        self,
        session_id: str,
        command,
        trigger: Optional[str] = None,
        newline: bool = False,
        fresh: bool = False,
        timeout: Optional[float] = None,
        encoding: Optional[str] = None,
        full: bool = False,
        keep_ansi: Optional[bool] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
        force: bool = False,
        cwd: Optional[str] = None,
        env: Optional[list] = None,
        output_path: Optional[str] = None,
        response_format: Optional[str] = None,
        svg_compression_level: Optional[int] = None,
        snapshot_diff: bool = False,
        size: Optional[str] = None,
        plugins: Optional[list] = None,
        mode: str = "pty",
    ):
        _logger.info(
            "cmd_exec: id=%r force=%s env=%s size=%s plugins=%s mode=%s",
            session_id,
            force,
            env,
            size,
            plugins,
            mode,
        )
        original_timeout = timeout
        timeout, keep_ansi, encoding, newline, send_eol = self._apply_config_defaults(
            timeout=timeout,
            keep_ansi=keep_ansi,
            encoding=encoding,
            newline=newline,
        )
        # 仅命令行显式传 --timeout 才进入等待模式；
        # set-default/--default 配置的 timeout 只作为等待时长的取值，
        # 不应把无触发参数的 read/exec/send 变成等待模式
        explicit_timeout = original_timeout is not None

        if mode == "subprocess" and size:
            print_response(
                Response.error(
                    "子进程模式不支持 --size（无终端）；请使用读 stdin 交互"
                )
            )
            return

        if response_format is None:
            response_format = self._config.get("response_format") or "stream"
        if svg_compression_level is None:
            svg_compression_level = self._config.get("svg_compression_level")
            if svg_compression_level is None:
                # 配置未设置时默认等级 1；注意 0 是合法值（不压缩），不能用 or 兜底
                svg_compression_level = 1

        # 命令拆分为参数列表（子进程模式也拆分，Popen 直接执行）
        if isinstance(command, str):
            if mode != "subprocess" and _has_shell_operators(command):
                if not force:
                    print_response(
                        Response.error(
                            "命令包含 shell 操作符 (| & > < && || ;)，"
                            "这些操作符需要 shell 解析，在 PTY 模式下无法工作。\n"
                            "  → 添加 --force-pty-mode 强制执行（操作符作为字面参数传递）"
                        )
                    )
                    return
                _logger.warning(
                    "--force-pty-mode: 忽略 shell 操作符检测，原样拆分执行, command=%r",
                    command,
                )
            command = shlex.split(command, posix=not IS_WINDOWS)
            # PowerShell/CMD 传递含空格路径时 -c 参数可能保留字面量双引号
            command = [s.strip('"') for s in command]

        msg = {
            "type": "exec",
            "id": session_id,
            "command": command,
            "newline": newline,
            "fresh": fresh,
            "full": full,
            "keep_ansi": keep_ansi,
            "timeout": timeout,
            "explicit_timeout": explicit_timeout,
            "mode": mode,
        }
        if trigger is not None:
            msg["trigger"] = trigger
        if encoding is not None:
            msg["encoding"] = encoding
        if idle_timeout is not None:
            msg["idle_timeout"] = idle_timeout
            msg["idle_after_first_output"] = idle_after_first_output
        msg["cwd"] = cwd if cwd is not None else os.getcwd()
        if env is not None:
            env_dict = {}
            for item in env:
                if "=" not in item:
                    print_response(
                        Response.error(
                            f"Invalid --env format: {item!r} (expected KEY=VALUE)"
                        )
                    )
                    return
                k, v = item.split("=", 1)
                env_dict[k] = v
            msg["env"] = env_dict
        if output_path:
            msg["include_screen_buffer"] = True
        if response_format == "svg":
            msg["include_screen_buffer"] = True
        if snapshot_diff:
            msg["snapshot_diff"] = True
        if plugins:
            # --plugin 按插件形态分流：CLI 形态在客户端启用本次调用，
            # 会话/进程形态透传 daemon 按现有逻辑挂载（避免 daemon 对 CLI 插件误报未加载）
            self._route_plugins(msg, plugins)

        # 终端尺寸：--size 优先，否则从 --default terminal-size 读取
        if size:
            from .config_manager import parse_terminal_size

            try:
                c, r = parse_terminal_size(size)
                msg["cols"] = c
                msg["rows"] = r
            except ValueError as e:
                print_response(Response.error(str(e)))
                return
        else:
            ts = self._config.get("terminal_size")
            if ts and ts != "80x24":
                from .config_manager import parse_terminal_size

                try:
                    c, r = parse_terminal_size(ts)
                    msg["cols"] = c
                    msg["rows"] = r
                except ValueError:
                    pass

        self._maybe_save_encoding(encoding)
        client_defaults = self._get_client_defaults()
        if client_defaults:
            msg["client_defaults"] = client_defaults

        resp = self._send_recv(msg, output_path=output_path)
        _decompress_screen_buffer(resp)
        self._merge_session_defaults(resp)

        if response_format == "svg":
            if resp.get("type") == "error":
                print_response(resp)
                return
            screen_buffer = resp.get("screenBuffer")
            if not screen_buffer:
                print_response(
                    Response.error(
                        "--response-format svg requires a screen buffer"
                    )
                )
                return
            from .renderer import _compress_svg, render_svg_string

            svg = render_svg_string(screen_buffer)
            svg = _compress_svg(svg, svg_compression_level)
            print_response({"type": "svg", "data": svg})
        elif output_path:
            display = {
                k: v
                for k, v in resp.items()
                if k not in (
                    "screenBuffer",
                    "screenBufferMeta",
                    "sessionDefaults",
                    "aiFileWritten",
                )
            }
            print_response(display)
        else:
            print_response(resp)
        # fileOutput 插件已自行写文件，跳过重复写入
        if not resp.get("aiFileWritten"):
            self._handle_output(
                output_path, resp, svg_compression_level=svg_compression_level
            )

    def cmd_read(
        self,
        session_id: str,
        trigger: Optional[str] = None,
        newline: bool = False,
        timeout: Optional[float] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
        lines: Optional[str] = None,
        grep: Optional[str] = None,
        offset: Optional[int] = None,
        encoding: Optional[str] = None,
        full: bool = False,
        keep_ansi: Optional[bool] = None,
        output_path: Optional[str] = None,
        response_format: Optional[str] = None,
        svg_compression_level: Optional[int] = None,
        snapshot_diff: bool = False,
        column: Optional[int] = None,
    ):
        """读取会话终端输出，支持触发条件等待"""
        _logger.info(
            "cmd_read: id=%r trigger=%r timeout=%s idle_timeout=%s lines=%s grep=%r offset=%s full=%s",
            session_id,
            trigger,
            timeout,
            idle_timeout,
            lines,
            grep,
            offset,
            full,
        )
        original_timeout = timeout
        timeout, keep_ansi, encoding, newline, _ = self._apply_config_defaults(
            timeout=timeout,
            keep_ansi=keep_ansi,
            encoding=encoding,
            newline=newline,
        )
        # 仅命令行显式传 --timeout 才进入等待模式；
        # set-default/--default 配置的 timeout 只作为等待时长的取值，
        # 不应把无触发参数的 read/exec/send 变成等待模式
        explicit_timeout = original_timeout is not None

        if response_format is None:
            response_format = self._config.get("response_format") or "stream"
        if svg_compression_level is None:
            svg_compression_level = self._config.get("svg_compression_level")
            if svg_compression_level is None:
                # 配置未设置时默认等级 1；注意 0 是合法值（不压缩），不能用 or 兜底
                svg_compression_level = 1

        msg = {
            "type": "read",
            "id": session_id,
            "full": full,
            "keep_ansi": keep_ansi,
            "timeout": timeout,
            "explicit_timeout": explicit_timeout,
        }
        if trigger is not None:
            msg["trigger"] = trigger
            msg["newline"] = newline
            msg["fresh"] = True
        if idle_timeout is not None:
            msg["idle_timeout"] = idle_timeout
            msg["idle_after_first_output"] = idle_after_first_output
        if lines is not None:
            msg["lines"] = lines
        if grep is not None:
            msg["grep"] = grep
        if offset is not None:
            msg["offset"] = offset
        if encoding is not None:
            msg["encoding"] = encoding
        if output_path:
            msg["include_screen_buffer"] = True
        if response_format == "svg":
            msg["include_screen_buffer"] = True
        if snapshot_diff:
            msg["snapshot_diff"] = True
        if column is not None:
            msg["column"] = column

        # CLI 插件按会话挂载自动挂钩（exec --plugin 挂载到会话后，此处自动回调）
        self._activate_session_cli(session_id)

        self._maybe_save_encoding(encoding)
        client_defaults = self._get_client_defaults()
        if client_defaults:
            msg["client_defaults"] = client_defaults

        resp = self._send_recv(msg, output_path=output_path)
        _decompress_screen_buffer(resp)
        self._merge_session_defaults(resp)

        if response_format == "svg":
            if resp.get("type") == "error":
                print_response(resp)
                return
            screen_buffer = resp.get("screenBuffer")
            if not screen_buffer:
                print_response(
                    Response.error(
                        "--response-format svg requires a screen buffer"
                    )
                )
                return
            from .renderer import _compress_svg, render_svg_string

            svg = render_svg_string(screen_buffer)
            svg = _compress_svg(svg, svg_compression_level)
            print_response({"type": "svg", "data": svg})
        elif output_path:
            display = {
                k: v
                for k, v in resp.items()
                if k not in (
                    "screenBuffer",
                    "screenBufferMeta",
                    "sessionDefaults",
                    "aiFileWritten",
                )
            }
            print_response(display)
        else:
            print_response(resp)
        # fileOutput 插件已自行写文件，跳过重复写入
        if not resp.get("aiFileWritten"):
            self._handle_output(
                output_path, resp, svg_compression_level=svg_compression_level
            )

    def cmd_send(
        self,
        session_id: str,
        input_text: str,
        trigger: Optional[str] = None,
        newline: bool = False,
        fresh: bool = False,
        timeout: Optional[float] = None,
        encoding: Optional[str] = None,
        full: bool = False,
        keep_ansi: Optional[bool] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
        json_escaping: bool = False,
        send_eol: Optional[str] = None,
        output_path: Optional[str] = None,
        response_format: Optional[str] = None,
        svg_compression_level: Optional[int] = None,
        snapshot_diff: bool = False,
    ):
        """向会话发送输入文本，支持触发条件等待和屏幕快照

        与 cmd_read 共享响应处理逻辑（trigger/svg/output），
        但额外处理输入文本：json_escaping 转义解码 + send_eol 行尾符追加。

        Args:
            session_id: 会话标识
            input_text: 要发送的原始输入文本
            trigger: 触发条件正则表达式（命中后返回输出）
            newline: 仅在换行后检查触发条件
            fresh: 是否从最新位置开始检测触发（CLI 传入 True）
            timeout: 等待超时秒数
            encoding: 终端编码
            full: 返回全部累积输出
            keep_ansi: 保留 ANSI 颜色/样式码
            idle_timeout: 输出静默超时
            idle_after_first_output: 仅首次输出后检测静默
            json_escaping: 启用 JSON + 控制字符转义解码
            send_eol: 行尾符名称（"cr"/"lf"/"crlf"/"none"），None 时用配置默认值
            output_path: 输出到文件
            response_format: 响应格式（stream/svg）
            svg_compression_level: SVG 压缩等级
            snapshot_diff: 仅返回屏幕变化行
        """
        _logger.info(
            "cmd_send: id=%r trigger=%r timeout=%s json_escaping=%s send_eol=%s",
            session_id,
            trigger,
            timeout,
            json_escaping,
            send_eol,
        )

        # send_eol: CLI 传入的是名称（"cr"/"lf"/"crlf"/"none"），转为实际字符
        send_eol_char = None
        if send_eol is not None:
            from .config_manager import _SEND_EOL_MAP

            send_eol_char = _SEND_EOL_MAP.get(send_eol, send_eol)

        original_timeout = timeout
        timeout, keep_ansi, encoding, newline, send_eol_resolved = (
            self._apply_config_defaults(
                timeout=timeout,
                keep_ansi=keep_ansi,
                encoding=encoding,
                newline=newline,
                send_eol=send_eol_char,
            )
        )
        # 仅命令行显式传 --timeout 才进入等待模式；
        # set-default/--default 配置的 timeout 只作为等待时长的取值，
        # 不应把无触发参数的 read/exec/send 变成等待模式
        explicit_timeout = original_timeout is not None

        # 处理输入文本：JSON 转义解码 + 行尾符追加
        input_processed = process_input(
            input_text,
            json_escaping=json_escaping,
            send_eol=send_eol_resolved,
        )

        if response_format is None:
            response_format = self._config.get("response_format") or "stream"
        if svg_compression_level is None:
            svg_compression_level = self._config.get("svg_compression_level")
            if svg_compression_level is None:
                # 配置未设置时默认等级 1；注意 0 是合法值（不压缩），不能用 or 兜底
                svg_compression_level = 1

        msg = {
            "type": "send",
            "id": session_id,
            "input": input_processed,
            "full": full,
            "keep_ansi": keep_ansi,
            "timeout": timeout,
            "explicit_timeout": explicit_timeout,
        }
        if trigger is not None:
            msg["trigger"] = trigger
            msg["newline"] = newline
            msg["fresh"] = fresh
        if idle_timeout is not None:
            msg["idle_timeout"] = idle_timeout
            msg["idle_after_first_output"] = idle_after_first_output
        if encoding is not None:
            msg["encoding"] = encoding
        if output_path:
            msg["include_screen_buffer"] = True
        if response_format == "svg":
            msg["include_screen_buffer"] = True
        if snapshot_diff:
            msg["snapshot_diff"] = True

        # CLI 插件按会话挂载自动挂钩（exec --plugin 挂载到会话后，此处自动回调）
        self._activate_session_cli(session_id)

        self._maybe_save_encoding(encoding)
        client_defaults = self._get_client_defaults()
        if client_defaults:
            msg["client_defaults"] = client_defaults

        resp = self._send_recv(msg, output_path=output_path)
        _decompress_screen_buffer(resp)
        self._merge_session_defaults(resp)

        if response_format == "svg":
            if resp.get("type") == "error":
                print_response(resp)
                return
            screen_buffer = resp.get("screenBuffer")
            if not screen_buffer:
                print_response(
                    Response.error(
                        "--response-format svg requires a screen buffer"
                    )
                )
                return
            from .renderer import _compress_svg, render_svg_string

            svg = render_svg_string(screen_buffer)
            svg = _compress_svg(svg, svg_compression_level)
            print_response({"type": "svg", "data": svg})
        elif output_path:
            display = {
                k: v
                for k, v in resp.items()
                if k not in (
                    "screenBuffer",
                    "screenBufferMeta",
                    "sessionDefaults",
                    "aiFileWritten",
                )
            }
            print_response(display)
        else:
            print_response(resp)
        # fileOutput 插件已自行写文件，跳过重复写入
        if not resp.get("aiFileWritten"):
            self._handle_output(
                output_path, resp, svg_compression_level=svg_compression_level
            )

    def cmd_list(self):
        """列出所有会话"""
        _logger.info("cmd_list")
        resp = self._send_recv({"type": "list"})
        print_response(resp)

    def cmd_plugin(
        self,
        action: str,
        session_id: Optional[str] = None,
        name: Optional[str] = None,
        command: Optional[str] = None,
        args: Optional[list] = None,
    ):
        """插件管理命令（list/ls/attach/detach/cmd）"""
        _logger.info(
            "cmd_plugin: action=%s id=%s name=%s cmd=%s",
            action,
            session_id,
            name,
            command,
        )
        msg = {"type": "plugin", "action": action}
        if session_id is not None:
            msg["id"] = session_id
        if name is not None:
            msg["name"] = name
        if command is not None:
            msg["command"] = command
        if args is not None:
            msg["args"] = args
        resp = self._send_recv(msg, autostart=False)
        print_response(resp)

    def cmd_wait(self, timeout: Optional[float] = None):
        """恒等待指定秒数（守护进程侧等待）

        Args:
            timeout: 等待秒数。None 时取 ConfigManager 的 timeout 配置（默认 120）。
        """
        if timeout is None:
            timeout = self._config.get("timeout")
        _logger.info("cmd_wait: timeout=%s", timeout)
        resp = self._send_recv({"type": "wait", "timeout": timeout})
        print_response(resp)

    def cmd_events(
        self,
        session_id: str,
        last: Optional[int] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ):
        """获取会话的所有事件"""
        _logger.info(
            "cmd_events: id=%r last=%s since=%s until=%s",
            session_id,
            last,
            since,
            until,
        )
        msg: dict = {"type": "events", "id": session_id}

        if since:
            msg["since"] = _parse_iso_time(since)
        if until:
            msg["until"] = _parse_iso_time(until)
        if last is not None:
            msg["last"] = last

        resp = self._send_recv(msg)
        print_response(resp)

    def cmd_file_read(
        self,
        path: str,
        cwd_session: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ):
        """读取文件内容（file read 子命令）

        路径原样传输，由 daemon 按会话 cwd（cwd_session 指定，不操作该会话）解析。
        """
        _logger.info(
            "cmd_file_read: path=%r cwd_session=%r offset=%s limit=%s",
            path,
            cwd_session,
            offset,
            limit,
        )
        msg: dict = {"type": "file_read", "path": path, "cwd_session": cwd_session}
        if offset is not None:
            msg["offset"] = offset
        if limit is not None:
            msg["limit"] = limit
        resp = self._send_recv(msg)
        print_response(resp)

    def cmd_file_write(
        self, path: str, cwd_session: str, content: Optional[str] = None
    ):
        """覆盖写/新建文件（file write 子命令）

        路径原样传输，由 daemon 按会话 cwd 解析；content 由调用方保证非空。
        """
        _logger.info(
            "cmd_file_write: path=%r cwd_session=%r content_len=%s",
            path,
            cwd_session,
            None if content is None else len(content),
        )
        msg: dict = {"type": "file_write", "path": path, "cwd_session": cwd_session}
        if content is not None:
            msg["content"] = content
        resp = self._send_recv(msg)
        print_response(resp)

    def cmd_file_edit(
        self, path: str, cwd_session: str, old: Optional[str], new: Optional[str]
    ):
        """唯一匹配替换/删除/新建（file edit 子命令）

        --old 空 = 新建（文件必须不存在）；--new 空 = 删除；
        均非空 = 替换（old 须唯一匹配）。
        CLI 侧将 None 归一为空串，daemon 恒收到字符串；
        路径由 daemon 按会话 cwd（cwd_session）解析。
        """
        _logger.info(
            "cmd_file_edit: path=%r cwd_session=%r old_len=%s new_len=%s",
            path,
            cwd_session,
            None if old is None else len(old),
            None if new is None else len(new),
        )
        resp = self._send_recv(
            {
                "type": "file_edit",
                "path": path,
                "cwd_session": cwd_session,
                "old": old or "",
                "new": new or "",
            }
        )
        print_response(resp)

    def cmd_file_grep(
        self,
        pattern: str,
        cwd_session: str,
        path: Optional[str] = None,
        include: Optional[str] = None,
        literal_text: bool = False,
    ):
        """内容搜索（file grep 子命令）

        path 缺省 = 会话 cwd（daemon 侧解析）；提供时按会话 cwd 展开。
        """
        _logger.info(
            "cmd_file_grep: pattern=%r cwd_session=%r path=%r include=%r literal=%s",
            pattern,
            cwd_session,
            path,
            include,
            literal_text,
        )
        msg: dict = {
            "type": "file_grep",
            "pattern": pattern,
            "cwd_session": cwd_session,
        }
        if path is not None:
            msg["path"] = path
        if include is not None:
            msg["include"] = include
        if literal_text:
            msg["literal_text"] = True
        resp = self._send_recv(msg)
        print_response(resp)

    def cmd_file_glob(self, pattern: str, cwd_session: str, path: Optional[str] = None):
        """文件名匹配（file glob 子命令）

        path 缺省 = 会话 cwd（daemon 侧解析）；提供时按会话 cwd 展开。
        """
        _logger.info(
            "cmd_file_glob: pattern=%r cwd_session=%r path=%r",
            pattern,
            cwd_session,
            path,
        )
        msg: dict = {
            "type": "file_glob",
            "pattern": pattern,
            "cwd_session": cwd_session,
        }
        if path is not None:
            msg["path"] = path
        resp = self._send_recv(msg)
        print_response(resp)

    def cmd_file_upload(
        self,
        local_path: str,
        remote_path: str,
        cwd_session: str,
        force: bool = False,
        timeout: Optional[float] = None,
    ):
        """上传本地文件/目录到 daemon 侧（file upload 子命令）

        - local_path 为 CLI 本机绝对路径（__main__ 已解析）
        - remote_path 由 daemon 按 cwd_session 会话 cwd 解析
        - 传输为多往返流式操作，不走 _send_recv：握手 JSON 后切换二进制帧
        """
        from ..config.transfer import TRANSFER_TIMEOUT
        from ..transfer.client_upload import upload
        from ..transfer.common import (
            TransferAbortedError,
            TransferError,
            TransferTimeoutError,
        )

        if timeout is None:
            timeout = TRANSFER_TIMEOUT
        _logger.info(
            "cmd_file_upload: local=%r remote=%r cwd_session=%r force=%s timeout=%s",
            local_path,
            remote_path,
            cwd_session,
            force,
            timeout,
        )
        sock = self._connect()
        try:
            # 握手消息凭证注入（与 _send_recv 一致：token/pubkey 认证字段）
            def enrich(msg: dict):
                if self._credential_provider is not None:
                    self._credential_provider.enrich(msg)

            resp = upload(
                sock,
                local_path,
                remote_path,
                cwd_session,
                force,
                timeout,
                enrich=enrich,
            )
            print_response(resp)
        except (TransferAbortedError, TransferError, TransferTimeoutError) as e:
            print_response(Response.error(str(e)))
        except (ConnectionError, socket.timeout, OSError) as e:
            _logger.warning("cmd_file_upload: 连接异常: %s", e)
            print_response(Response.error("transfer connection failed: %s" % e))
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def cmd_file_download(
        self,
        remote_path: str,
        local_path: str,
        cwd_session: str,
        force: bool = False,
        timeout: Optional[float] = None,
    ):
        """下载 daemon 侧文件/目录到本地（file download 子命令）

        - remote_path 由 daemon 按 cwd_session 会话 cwd 解析
        - local_path 为 CLI 本机绝对路径（__main__ 已解析）
        """
        from ..config.transfer import TRANSFER_TIMEOUT
        from ..transfer.client_download import download
        from ..transfer.common import (
            TransferAbortedError,
            TransferError,
            TransferTimeoutError,
        )

        if timeout is None:
            timeout = TRANSFER_TIMEOUT
        _logger.info(
            "cmd_file_download: remote=%r local=%r cwd_session=%r force=%s timeout=%s",
            remote_path,
            local_path,
            cwd_session,
            force,
            timeout,
        )
        sock = self._connect()
        try:
            # 握手消息凭证注入（与 _send_recv 一致：token/pubkey 认证字段）
            def enrich(msg: dict):
                if self._credential_provider is not None:
                    self._credential_provider.enrich(msg)

            resp = download(
                sock,
                local_path,
                remote_path,
                cwd_session,
                force,
                timeout,
                enrich=enrich,
            )
            print_response(resp)
        except (TransferAbortedError, TransferError, TransferTimeoutError) as e:
            print_response(Response.error(str(e)))
        except (ConnectionError, socket.timeout, OSError) as e:
            _logger.warning("cmd_file_download: 连接异常: %s", e)
            print_response(Response.error("transfer connection failed: %s" % e))
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def cmd_kill(self, session_id: str):
        """终止指定会话"""
        _logger.info("cmd_kill: id=%r", session_id)
        if not session_id or not isinstance(session_id, str):
            print_response(Response.error("invalid session id"))
            return
        from ..daemonctl import is_running

        if not is_running():
            print_response(
                {"commandType": "kill", "code": -1, "msg": "Daemon not running"}
            )
            return
        try:
            resp = self._send_recv({"type": "kill", "id": session_id})
        except ConnectionError:
            resp = {
                "commandType": "kill",
                "code": 0,
                "msg": "daemon not running, session likely dead",
            }
        except socket.timeout:
            resp = {
                "commandType": "kill",
                "code": 0,
                "msg": "daemon unresponsive, session likely dead",
            }
        except OSError:
            resp = {
                "commandType": "kill",
                "code": 0,
                "msg": "daemon connection failed, session likely dead",
            }
        print_response(resp)

    def cmd_closewin(self, session_id: str, hwnd: int):
        """关闭指定 GUI 窗口"""
        _logger.info("cmd_closewin: id=%r hwnd=0x%X", session_id, hwnd)
        resp = self._send_recv(
            {
                "type": "closewin",
                "id": session_id,
                "hwnd": hwnd,
            }
        )
        print_response(resp)

    def cmd_mouse(
        self,
        session_id: str,
        action: dict,
        trigger: Optional[str] = None,
        newline: bool = False,
        fresh: bool = False,
        timeout: Optional[float] = None,
        encoding: Optional[str] = None,
        keep_ansi: Optional[bool] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
        output_path: Optional[str] = None,
        response_format: Optional[str] = None,
        svg_compression_level: Optional[int] = None,
        snapshot_diff: bool = False,
    ):
        """发送鼠标动作到会话并等待输出

        Args:
            session_id: 会话标识
            action: 动作描述字典，由 CLI 解析后传入
        """
        _logger.info(
            "cmd_mouse: id=%r action=%s trigger=%r timeout=%s",
            session_id,
            action.get("action"),
            trigger,
            timeout,
        )
        original_timeout = timeout
        timeout, keep_ansi, encoding, newline, _ = self._apply_config_defaults(
            timeout=timeout,
            keep_ansi=keep_ansi,
            encoding=encoding,
            newline=newline,
        )
        # 仅命令行显式传 --timeout 才进入等待模式；
        # set-default/--default 配置的 timeout 只作为等待时长的取值，
        # 不应把无触发参数的 read/exec/send 变成等待模式
        explicit_timeout = original_timeout is not None

        if response_format is None:
            response_format = self._config.get("response_format") or "stream"
        if svg_compression_level is None:
            svg_compression_level = self._config.get("svg_compression_level")
            if svg_compression_level is None:
                # 配置未设置时默认等级 1；注意 0 是合法值（不压缩），不能用 or 兜底
                svg_compression_level = 1

        msg = {"type": "mouse", "id": session_id, **action}
        msg["newline"] = newline
        msg["fresh"] = fresh
        msg["keep_ansi"] = keep_ansi
        msg["timeout"] = timeout
        msg["explicit_timeout"] = explicit_timeout
        if trigger is not None:
            msg["trigger"] = trigger
        if encoding is not None:
            msg["encoding"] = encoding
        if idle_timeout is not None:
            msg["idle_timeout"] = idle_timeout
            msg["idle_after_first_output"] = idle_after_first_output
        if output_path:
            msg["include_screen_buffer"] = True
        if response_format == "svg":
            msg["include_screen_buffer"] = True
        if snapshot_diff:
            msg["snapshot_diff"] = True

        # CLI 插件按会话挂载自动挂钩（exec --plugin 挂载到会话后，此处自动回调）
        self._activate_session_cli(session_id)

        client_defaults = self._get_client_defaults()
        if client_defaults:
            msg["client_defaults"] = client_defaults

        resp = self._send_recv(msg, output_path=output_path)
        _decompress_screen_buffer(resp)
        self._merge_session_defaults(resp)

        if response_format == "svg":
            if resp.get("type") == "error":
                print_response(resp)
                return
            screen_buffer = resp.get("screenBuffer")
            if not screen_buffer:
                print_response(
                    Response.error(
                        "--response-format svg requires a screen buffer"
                    )
                )
                return
            from .renderer import _compress_svg, render_svg_string

            svg = render_svg_string(screen_buffer)
            svg = _compress_svg(svg, svg_compression_level)
            print_response({"type": "svg", "data": svg})
        elif output_path:
            display = {
                k: v
                for k, v in resp.items()
                if k not in (
                    "screenBuffer",
                    "screenBufferMeta",
                    "sessionDefaults",
                    "aiFileWritten",
                )
            }
            print_response(display)
        else:
            print_response(resp)
        # fileOutput 插件已自行写文件，跳过重复写入
        if not resp.get("aiFileWritten"):
            self._handle_output(
                output_path, resp, svg_compression_level=svg_compression_level
            )


    def cmd_workflow_run(
        self,
        file_path: str,
        vars_overrides: Optional[dict] = None,
        max_parallel: Optional[int] = None,
    ):
        """启动 workflow（YAML 定义文件，client 侧读取后发送 daemon 解析执行）"""
        _logger.info(
            "cmd_workflow_run: file=%r vars=%s max_parallel=%s",
            file_path,
            vars_overrides,
            max_parallel,
        )
        if not file_path:
            print_response(Response.error("workflow file path is required"))
            return
        try:
            with open(file_path, "rb") as f:
                data = f.read()
        except OSError as e:
            print_response(Response.error("读取 workflow 文件失败: %s" % e))
            return
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            print_response(Response.error("workflow 文件不是合法 UTF-8: %s" % e))
            return
        msg: dict = {"type": "workflow", "action": "run", "definition": text}
        if vars_overrides:
            msg["vars_override"] = vars_overrides
        if max_parallel is not None:
            msg["max_parallel"] = max_parallel
        resp = self._send_recv(msg, autostart=True)
        print_response(resp)

    def cmd_workflow_list(self):
        """列出所有 workflow 运行（含已结束）"""
        _logger.info("cmd_workflow_list")
        resp = self._send_recv({"type": "workflow", "action": "list"}, autostart=True)
        print_response(resp)

    def cmd_workflow_show(self, run_id: str):
        """查看单次 workflow 运行状态（步骤状态 + 日志）"""
        _logger.info("cmd_workflow_show: run_id=%r", run_id)
        if not run_id:
            print_response(Response.error("runId is required"))
            return
        resp = self._send_recv(
            {"type": "workflow", "action": "show", "runId": run_id}, autostart=True
        )
        print_response(resp)

    def cmd_workflow_cancel(self, run_id: str):
        """请求取消 workflow 运行"""
        _logger.info("cmd_workflow_cancel: run_id=%r", run_id)
        if not run_id:
            print_response(Response.error("runId is required"))
            return
        resp = self._send_recv(
            {"type": "workflow", "action": "cancel", "runId": run_id}, autostart=True
        )
        print_response(resp)
