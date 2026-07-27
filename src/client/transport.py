"""TCP 传输层 — Client 类

封装与守护进程的 TCP 通信，向 CLI 入口提供简洁的命令接口。
支持自动启动守护进程、临时默认配置覆盖。
"""

import logging
import os
import sys
import ssl
import json
import gzip
import base64
import socket
import shlex
import time
from typing import Optional

from ..protocol.message import Message
from ..protocol.response import Response
from ..auth.token import TokenCredentialProvider
from ..auth.token import HmacMessageSigner
from ..auth.pubkey import Ed25519MessageSigner
from ..auth.pubkey import PubkeyCredentialProvider
from ..auth.keys import PrivateKey
from ..auth.tls.known_hosts import KnownHosts
from .tls_transport import TLSClient
from ..ipc.shm import read_hmac_key
from ..config.common import DAEMON_HOST, IS_WINDOWS, DEFAULT_COLS, DEFAULT_ROWS
from ..config.client import (
    CONNECT_TIMEOUT,
    DEFAULT_TRIGGER_TIMEOUT,
    ENABLE_TOKEN_AUTH,
    ENABLE_PUBKEY_AUTH,
    CLIENT_AUTH_METHOD,
    PUBKEY_PRIVATE_KEY_PATH,
    DAEMON_REMOTE_HOST,
    DAEMON_REMOTE_PORT,
    KNOWN_HOSTS_FILE,
    TOFU_STRICT,
)


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


from ..daemon.lifecycle import is_running, start_daemon, stop_daemon
from .input import process_input
from .formatter import print_response
from .config_manager import ConfigManager, _DEFAULTS as _DEFAULTS_MAP
from .ai_analyser import analyse_response

_logger = logging.getLogger("pty-client")

_SHELL_OPS = frozenset({'|', '||', '&', '&&', ';', '>', '<', '>>'})


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


def _read_daemon_port() -> Optional[int]:
    from ..ipc.shm import read_port_from_shm
    return read_port_from_shm()


def _load_signer_and_providers():
    """按 CLIENT_AUTH_METHOD 单选装配签名器与凭证提供者

    客户端只能选一种认证方式，请求只携带一种凭证一种签名（OR 语义）：
    - "token":  HMAC 对称，出站签请求 + 入站验响应（双向保护）
    - "pubkey": Ed25519 非对称单向，出站签请求，入站不验响应（响应裸传）
    - "none":   无认证

    校验 CLIENT_AUTH_METHOD 在服务端 ENABLE 支持列表内（同机共享 common.toml）。
    设置 Message 出/入站签名器，返回 providers 列表供调用方使用。

    幂等：若 Message 出站签名器已设置则直接返回 None（已装配过）。
    """
    if Message.get_outbound_signer() is not None:
        return None

    method = CLIENT_AUTH_METHOD

    # 校验客户端选择的方式在服务端支持列表内（同机能力校验）
    if method == "token" and not ENABLE_TOKEN_AUTH:
        raise RuntimeError(
            "CLIENT_AUTH_METHOD=token 但服务端未启用 ENABLE_TOKEN_AUTH"
        )
    if method == "pubkey" and not ENABLE_PUBKEY_AUTH:
        raise RuntimeError(
            "CLIENT_AUTH_METHOD=pubkey 但服务端未启用 ENABLE_PUBKEY_AUTH"
        )

    providers = []

    if method == "token":
        # HMAC 对称：出站签请求 + 入站验响应，复用同一实例
        key = read_hmac_key()
        if key is None:
            _logger.warning("Token 认证已启用但无法从共享内存读取 HMAC 密钥")
        else:
            signer = HmacMessageSigner(key)
            Message.set_outbound_signer(signer)
            Message.set_inbound_verifier(signer)
            providers.append(TokenCredentialProvider())
            _logger.info("客户端认证方式: token (HMAC 双向)")

    elif method == "pubkey":
        # Ed25519 非对称单向：出站签请求，入站不验响应（无私钥验响应）
        try:
            private_key = PrivateKey.from_file(PUBKEY_PRIVATE_KEY_PATH)
        except (FileNotFoundError, PermissionError, ValueError) as e:
            _logger.error("加载 Ed25519 私钥失败 (%s): %s", PUBKEY_PRIVATE_KEY_PATH, e)
            raise
        Message.set_outbound_signer(Ed25519MessageSigner(private_key=private_key))
        Message.set_inbound_verifier(None)
        providers.append(PubkeyCredentialProvider(private_key))
        _logger.info("客户端认证方式: pubkey (Ed25519 单向)")

    else:
        # 无认证
        if ENABLE_TOKEN_AUTH or ENABLE_PUBKEY_AUTH:
            _logger.warning(
                "CLIENT_AUTH_METHOD=none 但服务端启用了认证，请求将被拒绝"
            )
        Message.set_outbound_signer(None)
        Message.set_inbound_verifier(None)
        _logger.warning("客户端认证方式: none (无认证)")

    return providers


class Client:
    """前端客户端，封装与守护进程的 TCP 通信

    支持两种连接模式：
    - 明文模式（token/none）：通过 SHM 发现同机 daemon，plain socket 连接
    - TLS 模式（pubkey 跨机）：通过 TLS 连接远程 daemon，TOFU 证书验证
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        config_overrides: Optional[dict] = None,
    ):
        """初始化客户端

        Args:
            host: CLI 覆盖远程 daemon 主机地址（pubkey 跨机 TLS 模式）。
                  None 时使用配置文件 DAEMON_REMOTE_HOST。
            port: CLI 覆盖远程 daemon TLS 端口（pubkey 跨机 TLS 模式）。
                  None 时使用配置文件 DAEMON_REMOTE_PORT。
            config_overrides: 配置覆盖字典。
        """
        self.host = DAEMON_HOST  # 明文模式连接地址（固定 127.0.0.1，SHM 发现）
        self._remote_host = host  # TLS 模式主机覆盖（None=用配置）
        self._remote_port = port  # TLS 模式端口覆盖（None=用配置）
        self._config = ConfigManager(overrides=config_overrides)
        # 凭证提供者懒加载：首次 _connect 时由 _load_signer_and_providers() 装配
        # 单选模式下 providers 只有 0 或 1 个：单 provider / None（无认证）
        self._credential_provider = None

    def _connect(self, autostart: bool = True) -> socket.socket:
        """连接守护进程（自动分流明文/TLS）

        pubkey 跨机模式（CLIENT_AUTH_METHOD=pubkey 且 DAEMON_REMOTE_HOST 非空）：
            → TLS 连接 + TOFU 验证（_connect_tls）
        其他模式（token/none 或同机 pubkey）：
            → 明文连接 + SHM 发现（_connect_plain）

        Args:
            autostart: 明文模式下守护进程未运行时是否自动启动。

        Returns:
            已连接的 socket（明文）或 SSLSocket（TLS）。
        """
        method = CLIENT_AUTH_METHOD
        is_remote = bool(self._remote_host or DAEMON_REMOTE_HOST)

        if method == "pubkey" and is_remote:
            return self._connect_tls()
        else:
            return self._connect_plain(autostart)

    def _connect_plain(self, autostart: bool = True) -> socket.socket:
        """明文连接守护进程（SHM 发现 + token/none 认证）

        通过共享内存发现 daemon 端口，创建 plain socket 连接，
        装配签名器与凭证提供者。autostart=True 时守护进程未运行则自动启动。
        """
        from ..daemon.lifecycle import _find_daemon_port, _ping_daemon

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
                print_response(Response.error("failed to start daemon (port not found in shm after start_daemon returned)"))
                sys.exit(1)

        last_err = None
        for attempt in range(5):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(CONNECT_TIMEOUT)
                sock.connect((self.host, port))
                _logger.info("已连接守护进程 %s:%s", self.host, port)
                # 懒装配签名器与凭证提供者（按 ENABLE_TOKEN_AUTH / ENABLE_PUBKEY_AUTH）
                # 幂等：已装配时返回 None，跳过 provider 重建
                providers = _load_signer_and_providers()
                if providers is not None:
                    # 单选模式：providers 只有 0 或 1 个，无需 CompositeCredentialProvider
                    self._credential_provider = providers[0] if providers else None
                return sock
            except ConnectionRefusedError as e:
                last_err = e
                _logger.debug("_connect_plain: attempt %d refused, retrying...", attempt + 1)
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

    def _connect_tls(self) -> ssl.SSLSocket:
        """TLS 连接守护进程（pubkey 跨机模式）

        1. 从 CLI 覆盖或配置文件获取远程 daemon 地址
        2. KnownHosts 加载 TOFU 信任存储
        3. TLSClient 建立 TLS 连接 + TOFU 证书验证
        4. 装配 Ed25519 签名器与凭证提供者
        """
        tls_host = self._remote_host or DAEMON_REMOTE_HOST
        tls_port = self._remote_port or DAEMON_REMOTE_PORT

        known_hosts = KnownHosts(KNOWN_HOSTS_FILE)
        tls_client = TLSClient(tls_host, tls_port, known_hosts, TOFU_STRICT)
        ssl_sock = tls_client.connect()
        _logger.info("已连接远程守护进程 (TLS) %s:%d", tls_host, tls_port)

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
        for key in ("timeout", "newline", "keep_ansi", "encoding", "debug",
                     "send_eol", "always_return_snapshot", "response_format",
                     "svg_compression_level"):
            val = cfg.get(key)
            if val is not None and val != _DEFAULTS_MAP.get(key):
                defaults[key] = val
        return defaults

    def _merge_session_defaults(self, resp: dict):
        session_defaults = resp.get("sessionDefaults")
        if not session_defaults or not isinstance(session_defaults, dict):
            return
        for key, val in session_defaults.items():
            if self._config.get(key) is None or self._config.get(key) == _DEFAULTS_MAP.get(key):
                try:
                    self._config.set(key, val)
                except (ValueError, KeyError):
                    pass

    def _maybe_save_encoding(self, encoding: Optional[str]):
        if encoding is not None and self._config.get("encoding") != encoding:
            self._config.set("encoding", encoding)

    @staticmethod
    def _handle_output(output_path: Optional[str], resp: dict,
                       svg_compression_level: int = 1):
        if not output_path:
            return
        if resp.get("type") == "error":
            _logger.warning("请求失败，跳过输出到 %s", output_path)
            return
        from .renderer import render_to_file, is_image_ext
        if is_image_ext(output_path) and not resp.get("screenBuffer"):
            print_response(
                Response.error(f"Image output requires --snapshot or --snapshot-mode (got --output {output_path})")
            )
            return
        err = render_to_file(output_path, resp, svg_compression_level=svg_compression_level)
        if err:
            print_response(Response.error(err))

    def _apply_ai_analysis(self, resp: dict, ai_analyse: str,
                           ai_prompt: Optional[str], output_file: Optional[str]) -> dict:
        """对 response 应用 AI 分析（--ai-analyse 钩子）

        封装 src/client/ai_analyser.analyse_response 调用：
        - none 模式直接返回原 resp（ai_analyser 内部短路）
        - fileOutput：调用前须确保 output_file 已写入（由调用方先 _handle_output）
        - responseOutput：把 outputStream 拼进 prompt

        提示词优先级：命令行 ai_prompt > --default ai-prompt > ai_analyser 内置默认。
        超时取自 src/config/common.toml 的 AICHAT_TIMEOUT。

        Args:
            resp:        守护进程返回的 response 字典。
            ai_analyse:  分析模式（none/fileOutput/responseOutput）。
            ai_prompt:   命令行/--default 传入的提示词，None 用内置默认。
            output_file: fileOutput 模式下的 -o 文件路径。

        Returns:
            分析后的 response（outputStream 可能被覆盖）；失败/none 时原样返回。
        """
        if not ai_analyse or ai_analyse == "none":
            return resp
        from ..config.common import AICHAT_TIMEOUT
        from .config_manager import _DEFAULTS as CFG_DEFAULTS
        prompt = ai_prompt or CFG_DEFAULTS.get("ai_prompt")
        return analyse_response(
            resp,
            mode=ai_analyse,
            prompt=prompt,
            output_file=output_file,
            timeout=AICHAT_TIMEOUT,
        )

    def _send_recv(self, msg: dict, *, autostart: bool = True) -> dict:
        sock = self._connect(autostart=autostart)
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
            return resp or Response.error("no response")
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
            from ..daemon.lifecycle import is_running
            if is_running():
                resp = self._send_recv({"type": "list"}, autostart=False)
                print_response(resp)

    def cmd_stop(self, force: bool = False):
        _logger.info("cmd_stop force=%s", force)
        stop_daemon(
            force=force,
            remote_host=self._remote_host,
            remote_port=self._remote_port,
        )

    def cmd_status(self):
        _logger.info("cmd_status")
        from ..daemon.lifecycle import _find_daemon_port, _find_daemon_pid
        port = _find_daemon_port()
        if port is None:
            print_response({"type": "status", "running": False})
            return
        pid = _find_daemon_pid()
        try:
            resp = self._send_recv({"type": "status"}, autostart=False)
            print_response(resp)
        except SystemExit:
            print_response({"type": "status", "running": False})
        except Exception as e:
            print_response({"type": "status", "running": True, "pid": pid, "port": port, "message": str(e)})

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
        snapshot_mode: bool = False,
        output_path: Optional[str] = None,
        response_format: Optional[str] = None,
        svg_compression_level: Optional[int] = None,
        snapshot_diff: bool = False,
        size: Optional[str] = None,
        ai_analyse: str = "none",
        ai_prompt: Optional[str] = None,
    ):
        _logger.info("cmd_exec: id=%r force=%s env=%s snapshot_mode=%s size=%s ai_analyse=%s",
                     session_id, force, env, snapshot_mode, size, ai_analyse)
        original_timeout = timeout
        timeout, keep_ansi, encoding, newline, send_eol = self._apply_config_defaults(
            timeout=timeout, keep_ansi=keep_ansi, encoding=encoding, newline=newline,
        )
        explicit_timeout = original_timeout is not None or timeout != 120.0

        if not snapshot_mode and self._config.get("always_return_snapshot"):
            snapshot_mode = True

        if response_format is None:
            response_format = self._config.get("response_format") or "stream"
        if svg_compression_level is None:
            svg_compression_level = self._config.get("svg_compression_level") or 1

        # 命令总是拆分为参数列表（PTY 模式不经过 shell）
        if isinstance(command, str):
            if _has_shell_operators(command):
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
                    "--force-pty-mode: 忽略 shell 操作符检测，原样拆分执行, command=%r", command,
                )
            command = shlex.split(command, posix=not IS_WINDOWS)
            # PowerShell/CMD 传递含空格路径时 -c 参数可能保留字面量双引号
            command = [s.strip('"') for s in command]

        msg = {
            "type": "exec", "id": session_id, "command": command,
            "newline": newline, "fresh": fresh, "full": full,
            "keep_ansi": keep_ansi, "timeout": timeout,
            "explicit_timeout": explicit_timeout,
        }
        if snapshot_mode:
            msg["snapshot_mode"] = True
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
                        Response.error(f"Invalid --env format: {item!r} (expected KEY=VALUE)")
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

        resp = self._send_recv(msg)
        _decompress_screen_buffer(resp)
        self._merge_session_defaults(resp)

        # AI 分析钩子：
        # - fileOutput：必须先写 -o 文件，AI 才能 aichat -f 读它；故先 _handle_output 再分析
        # - responseOutput：直接基于内存中的 outputStream 分析，不需要文件
        # - none：跳过
        if ai_analyse == "fileOutput":
            if not output_path:
                print_response(Response.error(
                    "--ai-analyse fileOutput requires -o/--output"
                ))
                return
            self._handle_output(output_path, resp, svg_compression_level=svg_compression_level)
            resp = self._apply_ai_analysis(resp, ai_analyse, ai_prompt, output_path)
        elif ai_analyse == "responseOutput":
            resp = self._apply_ai_analysis(resp, ai_analyse, ai_prompt, None)

        if response_format == "svg":
            if resp.get("type") == "error":
                print_response(resp)
                return
            screen_buffer = resp.get("screenBuffer")
            if not screen_buffer:
                print_response(Response.error(
                    "--response-format svg requires snapshot mode (--snapshot for mouse; or --default always-return-snapshot on)"))
                return
            from .renderer import render_svg_string, _compress_svg
            svg = render_svg_string(screen_buffer)
            svg = _compress_svg(svg, svg_compression_level)
            print_response({"type": "svg", "data": svg})
        elif output_path:
            display = {k: v for k, v in resp.items() if k not in ("screenBuffer", "screenBufferMeta", "sessionDefaults")}
            print_response(display)
        else:
            print_response(resp)
        # fileOutput 模式已在上方先写过文件，这里跳过重复写入
        if ai_analyse != "fileOutput":
            self._handle_output(output_path, resp, svg_compression_level=svg_compression_level)

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
        snapshot: bool = False,
        output_path: Optional[str] = None,
        response_format: Optional[str] = None,
        svg_compression_level: Optional[int] = None,
        snapshot_diff: bool = False,
        column: Optional[int] = None,
        ai_analyse: str = "none",
        ai_prompt: Optional[str] = None,
    ):
        """读取会话终端输出，支持触发条件等待"""
        _logger.info("cmd_read: id=%r trigger=%r timeout=%s idle_timeout=%s lines=%s grep=%r offset=%s full=%s snapshot=%s ai_analyse=%s",
                     session_id, trigger, timeout, idle_timeout, lines, grep, offset, full, snapshot, ai_analyse)
        original_timeout = timeout
        timeout, keep_ansi, encoding, newline, _ = self._apply_config_defaults(
            timeout=timeout, keep_ansi=keep_ansi, encoding=encoding, newline=newline,
        )
        explicit_timeout = original_timeout is not None or timeout != 120.0

        if not snapshot and self._config.get("always_return_snapshot"):
            snapshot = True

        if response_format is None:
            response_format = self._config.get("response_format") or "stream"
        if svg_compression_level is None:
            svg_compression_level = self._config.get("svg_compression_level") or 1

        msg = {
            "type": "read", "id": session_id,
            "full": full, "keep_ansi": keep_ansi,
            "timeout": timeout, "explicit_timeout": explicit_timeout,
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
        if snapshot:
            msg["snapshot"] = True
        if output_path:
            msg["include_screen_buffer"] = True
        if response_format == "svg":
            msg["include_screen_buffer"] = True
        if snapshot_diff:
            msg["snapshot_diff"] = True
        if column is not None:
            msg["column"] = column

        self._maybe_save_encoding(encoding)
        client_defaults = self._get_client_defaults()
        if client_defaults:
            msg["client_defaults"] = client_defaults

        resp = self._send_recv(msg)
        _decompress_screen_buffer(resp)
        self._merge_session_defaults(resp)

        # AI 分析钩子（同 cmd_exec：fileOutput 先写文件再分析，responseOutput 直接分析）
        if ai_analyse == "fileOutput":
            if not output_path:
                print_response(Response.error(
                    "--ai-analyse fileOutput requires -o/--output"
                ))
                return
            self._handle_output(output_path, resp, svg_compression_level=svg_compression_level)
            resp = self._apply_ai_analysis(resp, ai_analyse, ai_prompt, output_path)
        elif ai_analyse == "responseOutput":
            resp = self._apply_ai_analysis(resp, ai_analyse, ai_prompt, None)

        if response_format == "svg":
            if resp.get("type") == "error":
                print_response(resp)
                return
            screen_buffer = resp.get("screenBuffer")
            if not screen_buffer:
                print_response(Response.error(
                    "--response-format svg requires snapshot mode (--snapshot-mode for exec; --snapshot for send/read; or --default always-return-snapshot on)"))
                return
            from .renderer import render_svg_string, _compress_svg
            svg = render_svg_string(screen_buffer)
            svg = _compress_svg(svg, svg_compression_level)
            print_response({"type": "svg", "data": svg})
        elif output_path:
            display = {k: v for k, v in resp.items() if k not in ("screenBuffer", "screenBufferMeta", "sessionDefaults")}
            print_response(display)
        else:
            print_response(resp)
        # fileOutput 模式已在上方先写过文件，这里跳过重复写入
        if ai_analyse != "fileOutput":
            self._handle_output(output_path, resp, svg_compression_level=svg_compression_level)

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
        snapshot: bool = False,
        output_path: Optional[str] = None,
        response_format: Optional[str] = None,
        svg_compression_level: Optional[int] = None,
        snapshot_diff: bool = False,
        ai_analyse: str = "none",
        ai_prompt: Optional[str] = None,
    ):
        """向会话发送输入文本，支持触发条件等待和屏幕快照

        与 cmd_read 共享响应处理逻辑（trigger/snapshot/svg/output），
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
            snapshot: 返回屏幕快照
            output_path: 输出到文件
            response_format: 响应格式（stream/svg）
            svg_compression_level: SVG 压缩等级
            snapshot_diff: 仅返回屏幕变化行
        """
        _logger.info("cmd_send: id=%r trigger=%r timeout=%s json_escaping=%s send_eol=%s ai_analyse=%s",
                     session_id, trigger, timeout, json_escaping, send_eol, ai_analyse)

        # send_eol: CLI 传入的是名称（"cr"/"lf"/"crlf"/"none"），转为实际字符
        send_eol_char = None
        if send_eol is not None:
            from .config_manager import _SEND_EOL_MAP
            send_eol_char = _SEND_EOL_MAP.get(send_eol, send_eol)

        original_timeout = timeout
        timeout, keep_ansi, encoding, newline, send_eol_resolved = self._apply_config_defaults(
            timeout=timeout, keep_ansi=keep_ansi, encoding=encoding, newline=newline,
            send_eol=send_eol_char,
        )
        explicit_timeout = original_timeout is not None or timeout != 120.0

        # 处理输入文本：JSON 转义解码 + 行尾符追加
        input_processed = process_input(
            input_text, json_escaping=json_escaping, send_eol=send_eol_resolved,
        )

        if not snapshot and self._config.get("always_return_snapshot"):
            snapshot = True

        if response_format is None:
            response_format = self._config.get("response_format") or "stream"
        if svg_compression_level is None:
            svg_compression_level = self._config.get("svg_compression_level") or 1

        msg = {
            "type": "send", "id": session_id, "input": input_processed,
            "full": full, "keep_ansi": keep_ansi,
            "timeout": timeout, "explicit_timeout": explicit_timeout,
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
        if snapshot:
            msg["snapshot"] = True
        if output_path:
            msg["include_screen_buffer"] = True
        if response_format == "svg":
            msg["include_screen_buffer"] = True
        if snapshot_diff:
            msg["snapshot_diff"] = True

        self._maybe_save_encoding(encoding)
        client_defaults = self._get_client_defaults()
        if client_defaults:
            msg["client_defaults"] = client_defaults

        resp = self._send_recv(msg)
        _decompress_screen_buffer(resp)
        self._merge_session_defaults(resp)

        # AI 分析钩子（同 cmd_exec：fileOutput 先写文件再分析，responseOutput 直接分析）
        if ai_analyse == "fileOutput":
            if not output_path:
                print_response(Response.error(
                    "--ai-analyse fileOutput requires -o/--output"
                ))
                return
            self._handle_output(output_path, resp, svg_compression_level=svg_compression_level)
            resp = self._apply_ai_analysis(resp, ai_analyse, ai_prompt, output_path)
        elif ai_analyse == "responseOutput":
            resp = self._apply_ai_analysis(resp, ai_analyse, ai_prompt, None)

        if response_format == "svg":
            if resp.get("type") == "error":
                print_response(resp)
                return
            screen_buffer = resp.get("screenBuffer")
            if not screen_buffer:
                print_response(Response.error(
                    "--response-format svg requires snapshot mode (--snapshot for send/read; or --default always-return-snapshot on)"))
                return
            from .renderer import render_svg_string, _compress_svg
            svg = render_svg_string(screen_buffer)
            svg = _compress_svg(svg, svg_compression_level)
            print_response({"type": "svg", "data": svg})
        elif output_path:
            display = {k: v for k, v in resp.items() if k not in ("screenBuffer", "screenBufferMeta", "sessionDefaults")}
            print_response(display)
        else:
            print_response(resp)
        # fileOutput 模式已在上方先写过文件，这里跳过重复写入
        if ai_analyse != "fileOutput":
            self._handle_output(output_path, resp, svg_compression_level=svg_compression_level)

    def cmd_list(self):
        """列出所有会话"""
        _logger.info("cmd_list")
        resp = self._send_recv({"type": "list"})
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

    def cmd_events(self, session_id: str,
                   last: Optional[int] = None,
                   since: Optional[str] = None,
                   until: Optional[str] = None):
        """获取会话的所有事件"""
        _logger.info("cmd_events: id=%r last=%s since=%s until=%s", session_id, last, since, until)
        msg: dict = {"type": "events", "id": session_id}

        if since:
            msg["since"] = _parse_iso_time(since)
        if until:
            msg["until"] = _parse_iso_time(until)
        if last is not None:
            msg["last"] = last

        resp = self._send_recv(msg)
        print_response(resp)

    def cmd_kill(self, session_id: str):
        """终止指定会话"""
        _logger.info("cmd_kill: id=%r", session_id)
        if not session_id or not isinstance(session_id, str):
            print_response(Response.error("invalid session id"))
            return
        from ..daemon.lifecycle import is_running
        if not is_running():
            print_response({"commandType": "kill", "code": -1, "msg": "Daemon not running"})
            return
        try:
            resp = self._send_recv({"type": "kill", "id": session_id})
        except ConnectionError:
            resp = {"commandType": "kill", "code": 0, "msg": "daemon not running, session likely dead"}
        except socket.timeout:
            resp = {"commandType": "kill", "code": 0, "msg": "daemon unresponsive, session likely dead"}
        except OSError:
            resp = {"commandType": "kill", "code": 0, "msg": "daemon connection failed, session likely dead"}
        print_response(resp)

    def cmd_closewin(self, session_id: str, hwnd: int):
        """关闭指定 GUI 窗口"""
        _logger.info("cmd_closewin: id=%r hwnd=0x%X", session_id, hwnd)
        resp = self._send_recv({
            "type": "closewin",
            "id": session_id,
            "hwnd": hwnd,
        })
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
        snapshot: bool = False,
        snapshot_diff: bool = False,
        ai_analyse: str = "none",
        ai_prompt: Optional[str] = None,
    ):
        """发送鼠标动作到会话并等待输出

        Args:
            session_id: 会话标识
            action: 动作描述字典，由 CLI 解析后传入
        """
        _logger.info("cmd_mouse: id=%r action=%s trigger=%r timeout=%s ai_analyse=%s",
                     session_id, action.get("action"), trigger, timeout, ai_analyse)
        original_timeout = timeout
        timeout, keep_ansi, encoding, newline, _ = self._apply_config_defaults(
            timeout=timeout, keep_ansi=keep_ansi, encoding=encoding, newline=newline,
        )
        explicit_timeout = original_timeout is not None or timeout != 120.0

        if response_format is None:
            response_format = self._config.get("response_format") or "stream"
        if svg_compression_level is None:
            svg_compression_level = self._config.get("svg_compression_level") or 1

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
        if snapshot:
            msg["snapshot"] = True
        if snapshot_diff:
            msg["snapshot_diff"] = True

        client_defaults = self._get_client_defaults()
        if client_defaults:
            msg["client_defaults"] = client_defaults

        resp = self._send_recv(msg)
        _decompress_screen_buffer(resp)
        self._merge_session_defaults(resp)

        # AI 分析钩子（同 cmd_exec：fileOutput 先写文件再分析，responseOutput 直接分析）
        if ai_analyse == "fileOutput":
            if not output_path:
                print_response(Response.error(
                    "--ai-analyse fileOutput requires -o/--output"
                ))
                return
            self._handle_output(output_path, resp, svg_compression_level=svg_compression_level)
            resp = self._apply_ai_analysis(resp, ai_analyse, ai_prompt, output_path)
        elif ai_analyse == "responseOutput":
            resp = self._apply_ai_analysis(resp, ai_analyse, ai_prompt, None)

        if response_format == "svg":
            if resp.get("type") == "error":
                print_response(resp)
                return
            screen_buffer = resp.get("screenBuffer")
            if not screen_buffer:
                print_response(Response.error(
                    "--response-format svg requires snapshot mode (--snapshot-mode for exec; --snapshot for send/read; or --default always-return-snapshot on)"))
                return
            from .renderer import render_svg_string, _compress_svg
            svg = render_svg_string(screen_buffer)
            svg = _compress_svg(svg, svg_compression_level)
            print_response({"type": "svg", "data": svg})
        elif output_path:
            display = {k: v for k, v in resp.items() if k not in ("screenBuffer", "screenBufferMeta", "sessionDefaults")}
            print_response(display)
        else:
            print_response(resp)
        # fileOutput 模式已在上方先写过文件，这里跳过重复写入
        if ai_analyse != "fileOutput":
            self._handle_output(output_path, resp, svg_compression_level=svg_compression_level)
