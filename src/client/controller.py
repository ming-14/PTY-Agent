"""守护进程管控 — 客户端侧的进程管理（与 daemon 包完全解耦）

负责守护进程的启动 / 停止 / 存活检测以及客户端日志配置。
只依赖 protocol 层（统一封装）、config 与同层的 input.safe_print，
不 import 任何 daemon 代码。

- 启动：以独立子进程方式运行 `python -m src.daemon`
- 检测：protocol.daemon_utils（共享内存 PID + 心跳）
- 停止：protocol.request.roundtrip 发送 stop 请求，失败回退强杀 PID
"""

import logging
import os
import signal
import sys
import time
import subprocess

from ..config import (
    LOG_DIR,
    LOG_MAX_BYTES,
    CLIENT_LOG_LEVEL,
    MANAGED_LOGGERS,
    DAEMON_START_TIMEOUT,
    DAEMON_START_POLL_INTERVAL,
    STOP_TIMEOUT,
    IS_WINDOWS,
)
from .input import safe_print
from ..protocol.daemon_utils import (
    daemon_running,
    find_daemon_pid,
    pid_exists,
    cleanup_shm_resources,
)
from ..protocol.request import roundtrip

_logger = logging.getLogger("pty-client")


def setup_client_logging():
    """前台模式日志配置：写入 <程序根>/logs/client.log

    多个客户端进程会并发追加同一文件，轮转的 rename 互相踩踏，
    因此这里不做轮转，只在启动时判断：已超 LOG_MAX_BYTES 则重开（截断），
    否则续写。
    """
    if CLIENT_LOG_LEVEL is None:
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "client.log")
    try:
        oversized = os.path.getsize(log_file) > LOG_MAX_BYTES
    except OSError:
        oversized = False
    fh = logging.FileHandler(log_file, encoding="utf-8",
                             mode="w" if oversized else "a")
    fh.setFormatter(logging.Formatter(
        "[pty-agent:client] %(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    level = getattr(logging, CLIENT_LOG_LEVEL.upper(), logging.DEBUG)
    # 用 config.MANAGED_LOGGERS 统一名单：客户端也会 import 到
    # backend.* 模块（如 _print_shell_info），漏配的名字会把 WARNING
    # 直接喷到 stderr，污染 CLI 输出。
    for name in MANAGED_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(fh)
        logger.setLevel(level)
        logger.propagate = False


def _print_shell_info():
    """输出当前环境支持的 shell 列表"""
    try:
        from ..backend.subprocess import format_shell_info
        safe_print(f"[pty-agent] {format_shell_info()}")
    except Exception:
        pass


def is_running() -> bool:
    """守护进程是否正在运行（共享内存 PID + 心跳）"""
    return daemon_running()


def start_daemon() -> bool:
    """启动守护进程（以独立子进程方式）

    Windows: DETACHED_PROCESS 创建独立子进程。
    Unix:    Popen + setsid 会话分离。
    启动前检查共享内存，防止重复启动。

    Returns:
        True 表示守护进程已就绪（本次启动成功或本就在运行）；
        False 表示等待 DAEMON_START_TIMEOUT 后仍未就绪。
    """
    if is_running():
        safe_print("[pty-agent] 守护进程已在运行中")
        return True

    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "daemon.log")

    src_parent = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))

    if IS_WINDOWS:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        STARTF_USESHOWWINDOW = 0x00000001
        SW_HIDE = 0
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = SW_HIDE
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

    for _ in range(int(DAEMON_START_TIMEOUT / DAEMON_START_POLL_INTERVAL) + 1):
        if is_running():
            safe_print("[pty-agent] 守护进程已启动")
            _print_shell_info()
            return True
        time.sleep(DAEMON_START_POLL_INTERVAL)

    safe_print("[pty-agent] 守护进程启动失败（超时）")
    return False


def stop_daemon():
    """停止守护进程

    依次尝试：共享内存 stop 请求 → 强制终止 PID。
    """
    pid = find_daemon_pid()
    if pid is None:
        safe_print("[pty-agent] 守护进程未运行")
        cleanup_shm_resources()
        return

    stopped = False
    try:
        resp = roundtrip({"type": "stop"}, timeout=STOP_TIMEOUT)
        stopped = resp.get("type") == "ok"
    except Exception as e:
        safe_print(f"[pty-agent] 共享内存停止失败: {e}")

    # 停止失败时，尝试通过 PID 强制终止
    if not stopped and pid_exists(pid):
        failure = None
        try:
            if IS_WINDOWS:
                # 不经 shell：直接调 taskkill，输出丢弃（等价旧式的 >nul 2>&1）
                rc = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                # 非零码最常见的原因是"进程已经不在了"——那正是我们要的结果，
                # 只有进程确实还活着才算失败，不能谎报已停止。
                if rc != 0 and pid_exists(pid):
                    failure = f"taskkill 退出码 {rc}"
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception as e:
            failure = str(e)
        if failure is None:
            safe_print(f"[pty-agent] 已强制终止守护进程 (PID {pid})")
            stopped = True
        else:
            safe_print(f"[pty-agent] 强制终止失败: {failure}")

    cleanup_shm_resources()

    if stopped:
        safe_print("[pty-agent] 守护进程已停止")


def ensure_daemon():
    """确保守护进程在运行，必要时自动启动

    Raises:
        SystemExit: 无法启动守护进程。
    """
    if is_running():
        return
    _logger.info("守护进程未运行，自动启动")
    if start_daemon():
        return
    # start_daemon 内部已等满一个 DAEMON_START_TIMEOUT 窗口；冷启动 / 杀软扫描
    # 可能更慢，故再给一个窗口后才判定失败。
    deadline = time.monotonic() + DAEMON_START_TIMEOUT
    while time.monotonic() < deadline:
        if is_running():
            return
        time.sleep(DAEMON_START_POLL_INTERVAL)
    _logger.error("启动守护进程失败")
    safe_print("error: failed to start daemon", file=sys.stderr)
    sys.exit(1)
