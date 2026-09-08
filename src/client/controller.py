"""守护进程管控 — 客户端侧的进程管理（与 daemon 包完全解耦）

负责守护进程的启动 / 停止 / 存活检测以及客户端日志配置。
只依赖 protocol 层（统一封装）与 config，不 import 任何 daemon 代码。

- 启动：以独立子进程方式运行 `python -m src.daemon`
- 检测：protocol.daemon_utils（共享内存 PID + 心跳）
- 停止：protocol.request.roundtrip 发送 stop 请求，失败回退强杀 PID
"""

import logging
import os
import sys
import time
import subprocess

from ..config import (
    LOG_DIR,
    CLIENT_LOG_LEVEL,
    DAEMON_START_TIMEOUT,
    STOP_TIMEOUT,
    IS_WINDOWS,
)
from ..protocol.daemon_utils import (
    daemon_running,
    find_daemon_pid,
    cleanup_shm_resources,
)
from ..protocol.request import roundtrip

_logger = logging.getLogger("pty-client")


def setup_client_logging():
    """前台模式日志配置：写入 <程序根>/logs/client.log"""
    if CLIENT_LOG_LEVEL is None:
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "client.log")
    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setFormatter(logging.Formatter(
        "[pty-agent:client] %(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    level = getattr(logging, CLIENT_LOG_LEVEL.upper(), logging.DEBUG)
    for name in ("pty-client", "pty-protocol", "pty-session", "pty-daemon"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(fh)
        logger.setLevel(level)
        logger.propagate = False


def _safe_print(text: str):
    """安全打印 UTF-8 文本到 stdout"""
    try:
        sys.stdout.buffer.write(text.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
    except Exception:
        pass


def _print_shell_info():
    """输出当前环境支持的 shell 列表"""
    try:
        from ..backend.subprocess import format_shell_info
        _safe_print(f"[pty-agent] {format_shell_info()}")
    except Exception:
        pass


def is_running() -> bool:
    """守护进程是否正在运行（共享内存 PID + 心跳）"""
    return daemon_running()


def start_daemon():
    """启动守护进程（以独立子进程方式）

    Windows: DETACHED_PROCESS 创建独立子进程。
    Unix:    双 fork 彻底守护化。
    启动前检查共享内存，防止重复启动。
    """
    if is_running():
        _safe_print("[pty-agent] 守护进程已在运行中")
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "daemon.log")

    src_parent = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))

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
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=err_log,
                cwd=src_parent,
                startupinfo=startupinfo,
            )
    else:
        # Unix：Popen 独立进程 + setsid 会话分离（守护化）
        with open(log_file, "a", encoding="utf-8") as err_log:
            subprocess.Popen(
                [sys.executable, "-m", "src.daemon"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=err_log,
                cwd=src_parent,
                start_new_session=True,
            )

    for _ in range(int(DAEMON_START_TIMEOUT / 0.3) + 1):
        if is_running():
            _safe_print("[pty-agent] 守护进程已启动")
            _print_shell_info()
            return
        time.sleep(0.3)

    _safe_print("[pty-agent] 守护进程启动失败（超时）")


def stop_daemon():
    """停止守护进程

    依次尝试：共享内存 stop 请求 → 强制终止 PID。
    """
    pid = find_daemon_pid()
    if pid is None:
        _safe_print("[pty-agent] 守护进程未运行")
        cleanup_shm_resources()
        return

    stopped = False
    try:
        resp = roundtrip({"type": "stop"}, timeout=STOP_TIMEOUT)
        stopped = resp.get("type") == "ok"
    except Exception as e:
        _safe_print(f"[pty-agent] 共享内存停止失败: {e}")

    # 停止失败时，尝试通过 PID 强制终止
    if not stopped and _pid_exists(pid):
        try:
            if IS_WINDOWS:
                os.system(f"taskkill /PID {pid} /F >nul 2>&1")
            else:
                os.kill(pid, 9)
            _safe_print(f"[pty-agent] 已强制终止守护进程 (PID {pid})")
            stopped = True
        except Exception as e:
            _safe_print(f"[pty-agent] 强制终止失败: {e}")

    cleanup_shm_resources()

    if stopped:
        _safe_print("[pty-agent] 守护进程已停止")


def ensure_daemon():
    """确保守护进程在运行，必要时自动启动

    Raises:
        SystemExit: 无法启动守护进程。
    """
    if is_running():
        return
    _logger.info("守护进程未运行，自动启动")
    start_daemon()
    deadline = time.monotonic() + DAEMON_START_TIMEOUT
    while time.monotonic() < deadline:
        if is_running():
            return
        time.sleep(0.2)
    _logger.error("启动守护进程失败")
    print("error: failed to start daemon", file=sys.stderr)
    sys.exit(1)


def _pid_exists(pid: int) -> bool:
    """进程存在性检查（供强杀回退使用）"""
    from ..protocol.daemon_utils import pid_exists
    return pid_exists(pid)