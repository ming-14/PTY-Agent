"""daemonctl — 守护进程生命周期控制

守护进程的启动/停止/探测（start_daemon / stop_daemon / is_running），
属 client 侧控制能力，供 CLI 与 transport 使用；daemon 自身入口在 src/daemon/lifecycle.py。
连接/停止按 client.toml [connection] 的 CONNECT_MODE 路由：
- token：本机，经单实例锁判断存活 + SHM 发现端口，凭据经 SHM 交换
- basic：明文，密码认证（空密码=无认证），直接连接目标端口
- tls：  TLS + pubkey 认证，KnownHosts TOFU 验证，用于远程 daemon
单实例锁定位于本机（Windows 命名互斥 / Unix flock），仅 token 模式使用。

本模块不依赖 daemon 侧任何模块：daemon 以独立子进程（python -m src.daemon）运行，
其余信息经协议/共享内存（认证令牌 + HMAC 密钥）交互。
"""

import json
import os
import socket
import subprocess
import sys
import time
from typing import Optional

from ..auth.token import HmacMessageSigner
from ..common.process import pid_exists
from ..common.shells import format_shell_info
from ..config.client import (
    BASIC_HOST,
    BASIC_PASSWORD,
    BASIC_PORT,
    CONNECT_MODE,
    IS_WINDOWS,
    KNOWN_HOSTS_FILE,
    PUBKEY_PRIVATE_KEY_PATH,
    TLS_HOST,
    TLS_PORT,
    TOFU_STRICT,
    TOKEN_HOST,
    TOKEN_PORT,
)
from ..config.shared import (
    DAEMON_START_POLL_INTERVAL,
    DAEMON_START_TIMEOUT,
    LOG_DIR,
    PING_TIMEOUT,
    PROCESS_EXIT_WAIT_INTERVAL,
    PROCESS_EXIT_WAIT_RETRIES,
    STOP_TIMEOUT,
)
from ..ipc.shm import (
    cleanup_all_shm,
    read_auth_token,
    read_hmac_key,
)
from ..ipc.single_instance import SingleInstanceLock
from ..protocol.message import Message
from ..logging import get_logger

_logger = get_logger("pty-daemonctl")


def _safe_print(text: str):
    """安全打印：始终输出 JSON 格式到 stdout"""
    try:
        msg = json.dumps({"type": "info", "message": text}, ensure_ascii=False)
        sys.stdout.buffer.write(msg.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
    except Exception:
        pass


def _print_shell_info():
    """输出当前环境支持的 shell 列表"""
    try:
        _safe_print(f"[pty-agent] {format_shell_info()}")
    except Exception:
        pass


def _cleanup_credentials():
    """清理共享内存残留（认证令牌 + HMAC 密钥）"""
    cleanup_all_shm()


def _ping_daemon(port: int) -> bool:
    """通过 ping-pong 探测指定端口的守护进程（委托 Message.ping）

    ping 消息走 dispatcher 的 ping 豁免（不校验认证），且 send 时 skip_sign=True。
    连接地址取当前 CONNECT_MODE 对应的监听器 host。
    """
    host = {"token": TOKEN_HOST, "basic": BASIC_HOST}.get(CONNECT_MODE, TLS_HOST)
    return Message.ping(host, port, PING_TIMEOUT)


def _find_daemon_port() -> Optional[int]:
    """查找正在运行的守护进程端口

    token 模式（本机）：单实例锁判断存活，返回 TOKEN_PORT。
    basic/tls 模式：目标端口配置固定，是否有 daemon 由连接探测决定。

    Returns:
        守护进程端口，未运行返回 None。
    """
    if CONNECT_MODE != "token":
        return {"basic": BASIC_PORT, "tls": TLS_PORT}[CONNECT_MODE]
    if not is_running():
        return None
    return TOKEN_PORT


def _find_daemon_pid() -> Optional[int]:
    """查找正在运行的守护进程 PID

    经单实例锁的持有者查询（SingleInstanceLock.find_owner_pid）。

    Returns:
        守护进程 PID，未找到返回 None。
    """
    if not is_running():
        return None
    return SingleInstanceLock.find_owner_pid()


def is_running() -> bool:
    """检查守护进程是否正在运行

    使用 Windows 命名互斥 / Unix flock 做硬性单实例判断。

    Returns:
        True 表示守护进程在运行。
    """
    return SingleInstanceLock().is_locked()


def _daemon_ready() -> bool:
    """daemon 就绪判定

    token 模式经单实例锁判断（同机唯一实例）；
    basic/tls 模式直接探测配置目标端口 ping（跨机/无锁多实例场景）。
    """
    if CONNECT_MODE == "token":
        return is_running()
    host = {"basic": BASIC_HOST, "tls": TLS_HOST}[CONNECT_MODE]
    port = {"basic": BASIC_PORT, "tls": TLS_PORT}[CONNECT_MODE]
    return _ping_daemon(port)  # 实连探测（ping 走 dispatcher 豁免认证）


def start_daemon():
    """启动守护进程（以子进程方式）

    监听位置完全由 daemon.toml [listener] 段控制，不传参数。
    token 模式启动前经单实例锁做硬性检查；basic/tls 模式不做锁判断
    （daemon 可按 SINGLE_INSTANCE=false 无锁多实例并存）。
    Windows: DETACHED_PROCESS 创建独立子进程。
    Unix:    双 fork 彻底守护化。
    """
    if CONNECT_MODE == "token" and SingleInstanceLock().is_locked():
        _safe_print(f"[pty-agent] Daemon already running (token 端口 {TOKEN_PORT})")
        return True

    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = (
        time.strftime("%Y%m%d-%H%M%S") + f".{int(time.time() * 1000) % 1000:03d}"
    )
    log_file = os.path.join(LOG_DIR, f"daemon-stderr-{timestamp}.log")

    src_parent = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    if IS_WINDOWS:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= 0x00000001
        startupinfo.wShowWindow = 0
        with open(log_file, "a", encoding="utf-8") as err_log:
            subprocess.Popen(
                [sys.executable, "-m", "src.daemon"],
                close_fds=True,
                creationflags=DETACHED_PROCESS
                | CREATE_NEW_PROCESS_GROUP
                | CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=err_log,
                cwd=src_parent,
                startupinfo=startupinfo,
            )
    else:
        # 双 fork 守护化 + exec 启动 daemon 入口（daemonctl 侧不直接调用 daemon 代码，
        # 与 Windows 子进程方式对齐，daemon 以独立进程运行）
        pid = os.fork()
        if pid > 0:
            os.waitpid(pid, 0)
            return
        os.setsid()
        pid2 = os.fork()
        if pid2 > 0:
            os._exit(0)
        os.chdir("/")
        with open(os.devnull, "r") as f:
            os.dup2(f.fileno(), 0)
        with open(os.devnull, "w") as f:
            os.dup2(f.fileno(), 1)
        with open(log_file, "a") as f:
            os.dup2(f.fileno(), 2)
        env = os.environ.copy()
        # cwd 已切到 /，src 包只能经 PYTHONPATH 定位（execve 显式传入）
        env["PYTHONPATH"] = src_parent + os.pathsep + env.get("PYTHONPATH", "")
        os.execve(sys.executable, [sys.executable, "-m", "src.daemon"], env)
        os._exit(0)  # exec 失败兜底（守护进程未启动，start_daemon 轮询会超时）

    for _ in range(int(DAEMON_START_TIMEOUT / DAEMON_START_POLL_INTERVAL) + 1):
        if _daemon_ready():
            _safe_print("[pty-agent] Daemon started")
            _print_shell_info()
            return False
        time.sleep(DAEMON_START_POLL_INTERVAL)

    _safe_print(
        f"[pty-agent] Daemon start failed (timeout), "
        f"端口 {TOKEN_PORT} may be occupied",
    )


def stop_daemon(force: bool = False):
    """停止守护进程

    按 CONNECT_MODE 路由：
    - tls：  通过 TLS 连接远程 daemon 停止（TLS_HOST:TLS_PORT）
    - basic：直接明文连接目标端口停止（BASIC_PASSWORD 非空时带密码）
    - token：通过共享内存查找守护进程，TCP stop → 强制 kill。
    force=True 时：先尝试普通 stop，失败后通过互斥锁找到 PID 直接 kill（token 本机）。

    Args:
        force: 强制清理（token 模式：端口丢失时通过互斥锁定位并终止）。
    """
    if CONNECT_MODE == "tls":
        _stop_via_tls(force)
        return
    if CONNECT_MODE == "basic":
        _stop_via_basic(BASIC_HOST, BASIC_PORT, force)
        return

    # token 模式：通过共享内存查找守护进程（本机）
    port = _find_daemon_port()
    if port is None:
        if SingleInstanceLock().is_locked():
            if force:
                _stop_daemon_force()
            else:
                _safe_print(
                    "[pty-agent] 守护进程未被正确清理（端口探测失败）。"
                    "使用 stop --force 强制清理"
                )
        else:
            _safe_print("[pty-agent] Daemon not running")
        _cleanup_credentials()
        return

    # 先协议停止（TCP stop 不需要 PID）；PID 定位走全系统句柄表扫描，
    # 仅在 TCP 失败且 force 时惰性执行（正常路径完全避免扫描）
    stopped = _try_stop_via_basic(TOKEN_HOST, port, use_shm_credentials=True)

    if not stopped:
        if force:
            pid = _find_daemon_pid()
            if pid is not None:
                stopped = _force_kill_pid(pid)
                if stopped:
                    for _ in range(PROCESS_EXIT_WAIT_RETRIES):
                        if not pid_exists(pid):
                            break
                        time.sleep(PROCESS_EXIT_WAIT_INTERVAL)
        if not stopped:
            _safe_print(
                "[pty-agent] Daemon stop failed. 使用 stop --force 强制清理"
            )
    else:
        # TCP 停止成功：轮询单实例锁等待 daemon 退出
        # （token 模式锁与存活等价，免句柄表扫描）
        for _ in range(PROCESS_EXIT_WAIT_RETRIES):
            if not is_running():
                break
            time.sleep(PROCESS_EXIT_WAIT_INTERVAL)

    _cleanup_credentials()

    if stopped:
        _safe_print("[pty-agent] Daemon stopped")


def _stop_via_basic(host: str, port: int, force: bool = False):
    """停止 basic 监听器（BASIC_PASSWORD 非空时携带密码 + HMAC 签名）

    force=True 且协议停止失败时，回退到本地强制终止（仅本机 daemon 存在时有效，
    与 tls 模式回退逻辑一致）。
    """
    if _try_stop_via_basic(host, port, use_shm_credentials=False):
        _safe_print("[pty-agent] Daemon stopped")
        return
    if not force:
        _safe_print("[pty-agent] Daemon stop failed. 使用 stop --force 强制清理")
        return
    _safe_print("[pty-agent] Daemon stop failed，尝试强制清理...")
    if SingleInstanceLock().is_locked():
        _stop_daemon_force()
    else:
        _safe_print("[pty-agent] Daemon not running")


def _stop_via_tls(force: bool):
    """停止 tls 监听器（TLS + pubkey 认证）

    TLS stop 失败（如 TOFU 指纹不匹配）且 force=True 时，回退到本地强制终止
    （仅本机 daemon 存在时有效）。
    """
    stopped = _try_stop_via_tls()
    if stopped:
        _safe_print("[pty-agent] Daemon stopped")
        return
    if not force:
        _safe_print(
            "[pty-agent] TLS stop 失败。使用 stop --force 强制清理（通过互斥锁定位本地进程）"
        )
        return
    _safe_print("[pty-agent] TLS stop 失败，尝试强制清理...")
    if SingleInstanceLock().is_locked():
        _stop_daemon_force()
    else:
        _safe_print("[pty-agent] Daemon not running")


def _try_stop_via_basic(host: str, port: int, use_shm_credentials: bool) -> bool:
    """通过明文 TCP 连接停止守护进程

    token 模式装配 HMAC 签名器（从 SHM 读取密钥）并携带 token 字段；
    basic 模式密码认证时（BASIC_PASSWORD 非空）装配同一密码的 HMAC 签名器
    并携带 password 字段，空密码时无认证无签名。
    函数返回前恢复原签名器：签名器为线程级隐式全局状态，
    若装配后不恢复会污染调用线程后续所有收发（如测试进程）。
    """
    prev_out = Message.get_outbound_signer()
    prev_in = Message.get_inbound_verifier()
    try:
        if prev_out is None:
            if use_shm_credentials:
                key = read_hmac_key()
                if key is not None:
                    Message.set_outbound_signer(HmacMessageSigner(key))
            elif BASIC_PASSWORD:
                # basic 密码认证：密码即 HMAC 密钥（与客户端连接装配一致）
                Message.set_outbound_signer(
                    HmacMessageSigner(BASIC_PASSWORD.encode("utf-8"))
                )

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(STOP_TIMEOUT)
        sock.connect((host, port))
        token_field = read_auth_token() or "" if use_shm_credentials else ""
        stop_msg = {"type": "stop", "token": token_field}
        if BASIC_PASSWORD:
            stop_msg["password"] = BASIC_PASSWORD
        Message.send(sock, stop_msg)
        resp = Message.recv(sock)
        sock.close()
        if resp and resp.get("commandType") == "stop" and resp.get("code") == 0:
            return True
        else:
            _safe_print(f"[pty-agent] Daemon stop failed (response: {resp})")
            return False
    except Exception as e:
        _safe_print(f"[pty-agent] TCP stop failed: {e}")
        return False
    finally:
        Message.set_outbound_signer(prev_out)
        Message.set_inbound_verifier(prev_in)


def _try_stop_via_tls() -> bool:
    """通过 TLS 连接停止远程守护进程（CONNECT_MODE=tls）

    1. KnownHosts + TLSClient 建立 TLS 连接 + TOFU 验证（TLS_HOST:TLS_PORT）
    2. 装配 Ed25519 签名器 + PubkeyCredentialProvider 注入 pubkey_fp
    3. 发送 stop 消息

    Returns:
        True 表示停止成功。
    """
    from ..auth.keys import PrivateKey
    from ..auth.pubkey import Ed25519MessageSigner, PubkeyCredentialProvider
    from ..auth.tls.known_hosts import KnownHosts
    from .tls import TLSClient

    tls_host, tls_port = TLS_HOST, TLS_PORT

    try:
        known_hosts = KnownHosts(KNOWN_HOSTS_FILE)
        tls_client = TLSClient(tls_host, tls_port, known_hosts, TOFU_STRICT)
        ssl_sock = tls_client.connect()
        _logger.info("已连接远程守护进程 (TLS) %s:%d，发送 stop", tls_host, tls_port)

        # 装配 Ed25519 签名器 + 凭证提供者
        prev_out = Message.get_outbound_signer()
        try:
            private_key = PrivateKey.from_file(PUBKEY_PRIVATE_KEY_PATH)
            if prev_out is None:
                Message.set_outbound_signer(
                    Ed25519MessageSigner(private_key=private_key)
                )

            msg = {"type": "stop"}
            PubkeyCredentialProvider(private_key).enrich(msg)

            Message.send(ssl_sock, msg)
            resp = Message.recv(ssl_sock)
        finally:
            Message.set_outbound_signer(prev_out)
        ssl_sock.close()

        if resp and resp.get("commandType") == "stop" and resp.get("code") == 0:
            return True
        else:
            _safe_print(f"[pty-agent] Daemon stop failed (response: {resp})")
            return False
    except Exception as e:
        _safe_print(f"[pty-agent] TLS stop failed: {e}")
        return False


def _force_kill_pid(pid) -> bool:
    """通过 PID 强制终止进程"""
    if not pid_exists(pid):
        return False
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                capture_output=True,
                check=False,
            )
        else:
            os.kill(pid, 9)
        _safe_print(f"[pty-agent] Daemon force-killed (PID {pid})")
        return True
    except Exception as e:
        _safe_print(f"[pty-agent] Force kill failed: {e}")
        return False


def _stop_daemon_force():
    """强制清理：互斥锁被占用但端口丢失时，通过互斥锁找到 PID 并终止"""
    owner_pid = SingleInstanceLock.find_owner_pid()
    if owner_pid is None:
        _safe_print("[pty-agent] 无法定位守护进程 PID，互斥锁可能已释放")
        return

    _safe_print(f"[pty-agent] 发现守护进程 (PID {owner_pid})，强制终止...")

    if _force_kill_pid(owner_pid):
        for _ in range(PROCESS_EXIT_WAIT_RETRIES):
            if not pid_exists(owner_pid):
                break
            time.sleep(PROCESS_EXIT_WAIT_INTERVAL)
        _safe_print("[pty-agent] Daemon stopped (force)")
