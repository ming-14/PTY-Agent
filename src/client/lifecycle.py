"""客户端侧 — 守护进程生命周期控制

守护进程的启动/停止/探测（start_daemon / stop_daemon / is_running / 端口发现），
属客户端控制能力，供 CLI 与 transport 使用；daemon 自身入口在 src/daemon/lifecycle.py。
端口动态分配：每次启动随机选取未被占用的端口，通过共享内存传递到客户端。
单实例检查：共享内存 + SingleInstanceLock（ipc/single_instance.py），ping 验证确保存活。
"""

import os
import sys
import time
import socket
import json
import logging
import subprocess
from typing import Optional

from ..config.common import (
    DAEMON_HOST,
    IS_WINDOWS,
)
from ..config.client import (
    CLIENT_AUTH_METHOD,
    DAEMON_REMOTE_HOST,
    DAEMON_REMOTE_PORT,
    KNOWN_HOSTS_FILE,
    TOFU_STRICT,
    PUBKEY_PRIVATE_KEY_PATH,
)
from ..config.daemon import (
    DEFAULT_DAEMON_PORT,
    DAEMON_START_TIMEOUT,
    DAEMON_START_POLL_INTERVAL,
    PROCESS_EXIT_WAIT_RETRIES,
    PROCESS_EXIT_WAIT_INTERVAL,
    PING_TIMEOUT,
    STOP_TIMEOUT,
)
from ..config.daemon import (
    LOG_DIR,
    CLIENT_LOG_LEVEL,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    CLIENT_LOGGERS,
    WEB_HOST,
    WEB_PORT,
)
from ..ipc.shm import (
    read_daemon_info_from_shm,
    read_port_from_shm,
    read_auth_token,
    cleanup_all_shm,
    read_hmac_key,
)
from ..ipc.single_instance import SingleInstanceLock
from ..auth.token import HmacMessageSigner
from ..protocol.message import Message
from ..process.info import pid_exists

_logger = logging.getLogger("pty-client")


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
        from ..pty import format_shell_info
        _safe_print(f"[pty-agent] {format_shell_info()}")
    except Exception:
        pass


def _find_free_port() -> int:
    """查找一个随机可用的 TCP 端口

    Returns:
        操作系统随机分配的可用端口号。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((DAEMON_HOST, 0))
        return s.getsockname()[1]


def _cleanup_port():
    """清理共享内存残留（端口 + 认证令牌 + HMAC 密钥）"""
    cleanup_all_shm()


# ============================================================
#  生命周期函数
# ============================================================


def _ping_daemon(port: int) -> bool:
    """通过 ping-pong 探测指定端口的守护进程（委托 Message.ping）

    ping 消息走 dispatcher 的 ping 豁免（不校验认证），且 send 时 skip_sign=True。
    """
    return Message.ping(DAEMON_HOST, port, PING_TIMEOUT)


def _find_daemon_port() -> Optional[int]:
    """查找正在运行的守护进程端口

    从共享内存读取 PID+端口，验证进程存活且 TCP 可 ping。
    用于 start_daemon 的单实例检查和 stop_daemon 的孤儿清理。

    Returns:
        守护进程端口，未找到返回 None。
    """
    info = read_daemon_info_from_shm()
    if info is None:
        return None

    pid, port = info

    # 进程不存在 → 僵死残留，清理
    if not pid_exists(pid):
        _logger.info("共享内存中的进程 %d 已不存在，清理残留", pid)
        _cleanup_port()
        return None

    # 进程存在但 ping 不通
    if not _ping_daemon(port):
        # 互斥体仍存在 → 守护进程可能在启动中，不应清理 shm
        if SingleInstanceLock().is_locked():
            _logger.debug(
                "进程 %d 端口 %d 暂时无响应，但互斥体存在，判定为启动中",
                pid, port,
            )
            return port
        _logger.info("进程 %d 存在但端口 %d 无响应，判定为僵死守护进程", pid, port)
        _cleanup_port()
        return None

    return port


def _find_daemon_pid() -> Optional[int]:
    """查找正在运行的守护进程 PID

    Returns:
        守护进程 PID，未找到返回 None。
    """
    info = read_daemon_info_from_shm()
    if info is None:
        return None

    pid, port = info

    if not pid_exists(pid):
        _cleanup_port()
        return None

    if not _ping_daemon(port):
        # 互斥体仍存在 → 守护进程可能在启动中，不应清理 shm
        if SingleInstanceLock().is_locked():
            _logger.debug(
                "进程 %d 端口 %d 暂时无响应，但互斥体存在，判定为启动中",
                pid, port,
            )
            return pid
        _logger.info("进程 %d 存在但端口 %d 无响应，判定为僵死守护进程", pid, port)
        _cleanup_port()
        return None

    return pid


def is_running() -> bool:
    """检查守护进程是否正在运行

    使用 Windows 命名互斥 / Unix flock 做硬性单实例判断。
    仅在确认无守护进程（互斥体不存在且 shm 中的进程已死）时清理残留。

    Returns:
        True 表示守护进程在运行。
    """
    if SingleInstanceLock().is_locked():
        return True
    # 互斥体不存在，检查 shm 中是否有僵死残留
    info = read_daemon_info_from_shm()
    if info is not None:
        pid, port = info
        if pid_exists(pid):
            # 进程存在但互斥体不存在 → 可能刚退出正在清理，不急于清 shm
            return False
        # 进程已死，清理残留
        _cleanup_port()
    return False


def start_daemon():
    """启动守护进程（以子进程方式）

    自动分配一个随机端口，通过共享内存传递到客户端。
    启动前使用 Windows 命名互斥 / Unix flock 做硬性单实例检查。
    Windows: DETACHED_PROCESS 创建独立子进程。
    Unix:    双 fork 彻底守护化。
    """
    if SingleInstanceLock().is_locked():
        port = read_port_from_shm()
        if port is not None:
            _safe_print(f"[pty-agent] Daemon already running (port {port})")
        else:
            _safe_print("[pty-agent] Daemon already running (port unknown, shared memory corrupted)")
        return True

    port = _find_free_port()

    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S") + f".{int(time.time() * 1000) % 1000:03d}"
    log_file = os.path.join(LOG_DIR, f"daemon-stderr-{timestamp}.log")

    src_parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    if IS_WINDOWS:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= 0x00000001
        startupinfo.wShowWindow = 0
        with open(log_file, "a", encoding="utf-8") as err_log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "src.daemon", "--port", str(port)],
                close_fds=True,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=err_log,
                cwd=src_parent,
                startupinfo=startupinfo,
            )
    else:
        # 双 fork 守护化 + exec 启动 daemon 入口（client 侧不直接调用 daemon 代码，
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
        env["PYTHONPATH"] = src_parent + os.pathsep + env.get("PYTHONPATH", "")
        os.execv(sys.executable, [sys.executable, "-m", "src.daemon", "--port", str(port)])
        os._exit(0)  # exec 失败兜底（守护进程未启动，start_daemon 轮询会超时）

    for _ in range(int(DAEMON_START_TIMEOUT / DAEMON_START_POLL_INTERVAL) + 1):
        if is_running():
            actual_port = read_port_from_shm()
            if actual_port is not None:
                _safe_print(f"[pty-agent] Daemon started (port {actual_port})")
            else:
                _safe_print("[pty-agent] Daemon started (port unknown)")
            _safe_print(f"[pty-agent] Web server: http://{WEB_HOST}:{WEB_PORT}/")
            _print_shell_info()
            return False
        time.sleep(DAEMON_START_POLL_INTERVAL)

    _safe_print(
        f"[pty-agent] Daemon start failed (timeout), "
        f"port {port} may be occupied",
    )


def stop_daemon(
    force: bool = False,
    remote_host: Optional[str] = None,
    remote_port: Optional[int] = None,
):
    """停止守护进程

    pubkey 跨机模式（TLS）：先通过 TLS 连接远程 daemon 停止；
        TLS stop 失败（如 TOFU 指纹不匹配）且 force=True 时，回退到本地强制终止。
    其他模式（明文）：通过共享内存查找守护进程，TCP stop → 强制 kill。
    force=True 时：先尝试普通 stop，失败后通过互斥锁找到 PID 直接 kill。

    Args:
        force: 强制清理（TLS 模式：TLS stop 失败后回退到本地 force-kill；
            明文模式：端口丢失时通过互斥锁定位并终止）。
        remote_host: CLI 覆盖远程 daemon 主机地址（TLS 模式）。
        remote_port: CLI 覆盖远程 daemon TLS 端口（TLS 模式）。
    """
    method = CLIENT_AUTH_METHOD
    is_remote = bool(remote_host or DAEMON_REMOTE_HOST)

    if method == "pubkey" and is_remote:
        # TLS 模式：先通过 TLS 连接远程 daemon 停止
        stopped = _try_stop_via_tls(remote_host, remote_port)
        if stopped:
            _safe_print("[pty-agent] Daemon stopped")
            return
        # TLS stop 失败（如 TOFU 指纹不匹配）：force=True 时回退到本地强制终止
        if not force:
            _safe_print(
                "[pty-agent] TLS stop 失败。使用 stop --force 强制清理（通过互斥锁定位本地进程）"
            )
            return
        _safe_print("[pty-agent] TLS stop 失败，尝试强制清理...")
        # Fall through 到下方明文模式的 force-kill 路径

    # 明文模式：通过共享内存查找守护进程
    port = _find_daemon_port()
    if port is None:
        if SingleInstanceLock().is_locked():
            if force:
                _stop_daemon_force()
            else:
                _safe_print(
                    "[pty-agent] 守护进程未被正确清理（共享内存端口信息丢失）。"
                    "使用 stop --force 强制清理"
                )
        else:
            _safe_print("[pty-agent] Daemon not running")
        _cleanup_port()
        return

    pid = _find_daemon_pid()
    stopped = _try_stop_daemon(port, pid)

    if not stopped and force and pid is not None:
        stopped = _force_kill_pid(pid)

    if stopped and pid is not None:
        for _ in range(PROCESS_EXIT_WAIT_RETRIES):
            if not pid_exists(pid):
                break
            time.sleep(PROCESS_EXIT_WAIT_INTERVAL)

    _cleanup_port()

    if stopped:
        _safe_print("[pty-agent] Daemon stopped")


def _try_stop_daemon(port, pid) -> bool:
    """尝试通过 TCP stop 或 PID kill 停止守护进程（明文模式路由）

    明文模式（token/none）：调用 _try_stop_via_plain，失败后 force-kill。
    """
    stopped = _try_stop_via_plain(port)

    if not stopped and pid is not None:
        stopped = _force_kill_pid(pid)

    return stopped


def _try_stop_via_plain(port) -> bool:
    """通过明文 TCP 连接停止守护进程（token/none 模式）

    装配 HMAC 签名器（从 SHM 读取密钥），发送 stop 消息。
    函数返回前恢复原签名器：签名器为线程级隐式全局状态，
    若装配后不恢复会污染调用线程后续所有收发（如测试进程）。
    """
    prev_out = Message.get_outbound_signer()
    prev_in = Message.get_inbound_verifier()
    try:
        if prev_out is None:
            key = read_hmac_key()
            if key is not None:
                Message.set_outbound_signer(HmacMessageSigner(key))

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(STOP_TIMEOUT)
        sock.connect((DAEMON_HOST, port))
        Message.send(sock, {"type": "stop", "token": read_auth_token() or ""})
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


def _try_stop_via_tls(
    remote_host: Optional[str] = None,
    remote_port: Optional[int] = None,
) -> bool:
    """通过 TLS 连接停止远程守护进程（pubkey 跨机模式）

    1. KnownHosts + TLSClient 建立 TLS 连接 + TOFU 验证
    2. 装配 Ed25519 签名器 + PubkeyCredentialProvider 注入 pubkey_fp
    3. 发送 stop 消息

    Args:
        remote_host: CLI 覆盖远程 daemon 主机地址（None=用配置）。
        remote_port: CLI 覆盖远程 daemon TLS 端口（None=用配置）。

    Returns:
        True 表示停止成功。
    """
    from .tls_transport import TLSClient
    from ..auth.tls.known_hosts import KnownHosts
    from ..auth.pubkey import Ed25519MessageSigner
    from ..auth.pubkey import PubkeyCredentialProvider
    from ..auth.keys import PrivateKey

    tls_host = remote_host or DAEMON_REMOTE_HOST
    tls_port = remote_port or DAEMON_REMOTE_PORT

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
                Message.set_outbound_signer(Ed25519MessageSigner(private_key=private_key))

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
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                           capture_output=True)
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


# ============================================================
#  客户端日志配置
# ============================================================


def setup_client_logging():
    """客户端日志配置：写入 <用户目录>/.pty-agent/logs/client-{时间戳}.log

    为 pty-client 等前台相关 logger 配置文件输出。
    CLIENT_LOG_LEVEL 设为 None 则不配置日志。
    """
    if CLIENT_LOG_LEVEL is None:
        return
    from ..logging_setup import configure_log_files
    level = getattr(logging, CLIENT_LOG_LEVEL.upper(), logging.DEBUG)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    configure_log_files(LOG_DIR, {"client": CLIENT_LOGGERS}, {"client": level}, formatter)