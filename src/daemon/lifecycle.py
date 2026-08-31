"""守护进程层 — 生命周期管理

提供守护进程的启动/停止/检测函数和入口 main()。
单实例检测：纯共享内存（PID + 状态 + 心跳），PID 存在且心跳新鲜即视为存活，
无 TCP ping、无端口、无锁文件。
"""

import os
import sys
import time
import json
import logging
import subprocess
from typing import Optional

from ..config import (
    LOG_DIR,
    DAEMON_LOG_LEVEL,
    CLIENT_LOG_LEVEL,
    DAEMON_START_TIMEOUT,
    DAEMON_HEARTBEAT_FRESH,
    STOP_TIMEOUT,
    REQ_SHM_SIZE,
    RESP_SHM_SIZE,
    IS_WINDOWS,
)
from ..protocol.shm import (
    read_daemon_info,
    cleanup_daemon_info,
    Mailbox,
    make_channel_names,
    read_message,
    write_message,
    _DATA_BODY_OFF,
)
from ..protocol.shm_utils import open_shm, close_shm
from ..session.shm_utils import (
    read_auth_token,
    cleanup_auth_shm,
)

_logger = logging.getLogger("pty-daemon")

_json_mode = False


def set_json_mode(enabled: bool):
    global _json_mode
    _json_mode = enabled


def _safe_print(text: str):
    """安全打印：JSON 模式下输出 JSON，否则 UTF-8 文本"""
    try:
        if _json_mode:
            msg = json.dumps({"type": "info", "message": text}, ensure_ascii=False)
            sys.stdout.buffer.write(msg.encode("utf-8") + b"\n")
        else:
            sys.stdout.buffer.write(text.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
    except Exception:
        pass


def _print_shell_info():
    """输出当前环境支持的 shell 列表"""
    try:
        from ..pty.subprocess import format_shell_info
        _safe_print(f"[pty-agent] {format_shell_info()}")
    except Exception:
        pass


def _cleanup_shm_resources():
    """清理共享内存残留（守护进程信息区 + 认证令牌）"""
    cleanup_daemon_info()
    cleanup_auth_shm()


# ============================================================
#  生命周期函数
# ============================================================


def _pid_exists(pid: int) -> bool:
    """检查指定 PID 的进程是否存在

    Args:
        pid: 进程 ID。

    Returns:
        True 表示进程存在。
    """
    if IS_WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


def _heartbeat_fresh(heartbeat: float) -> bool:
    """判断心跳时间戳是否新鲜

    Args:
        heartbeat: 心跳时间戳（time.time()）。

    Returns:
        True 表示心跳新鲜（守护进程存活）。
    """
    return (time.time() - heartbeat) <= DAEMON_HEARTBEAT_FRESH


def _find_daemon_pid() -> Optional[int]:
    """查找正在运行的守护进程 PID（纯共享内存）

    从共享内存读取 PID + 心跳，进程存在且心跳新鲜视为存活；
    否则清理残留返回 None。

    Returns:
        守护进程 PID，未找到返回 None。
    """
    info = read_daemon_info()
    if info is None:
        return None

    pid, running, heartbeat = info

    if not running:
        _logger.info("共享内存中的守护进程已标记停止，清理残留")
        _cleanup_shm_resources()
        return None

    if not _pid_exists(pid):
        _logger.info("共享内存中的进程 %d 已不存在，清理残留", pid)
        _cleanup_shm_resources()
        return None

    if not _heartbeat_fresh(heartbeat):
        _logger.info("进程 %d 心跳过期（%.1fs 前），判定为僵死守护进程",
                     pid, time.time() - heartbeat)
        _cleanup_shm_resources()
        return None

    return pid


def is_running() -> bool:
    """检查守护进程是否正在运行

    Returns:
        True 表示守护进程在运行。
    """
    return _find_daemon_pid() is not None


def start_daemon():
    """启动守护进程（以子进程方式）

    启动前检查共享内存，防止重复启动。
    Windows: DETACHED_PROCESS 创建独立子进程。
    Unix:    双 fork 彻底守护化。
    """
    if is_running():
        _safe_print("[pty-agent] 守护进程已在运行中")
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "daemon.log")

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
        sys.argv = ["src.daemon"]

    for _ in range(int(DAEMON_START_TIMEOUT / 0.3) + 1):
        if is_running():
            _safe_print("[pty-agent] 守护进程已启动")
            _print_shell_info()
            return
        time.sleep(0.3)

    _safe_print("[pty-agent] 守护进程启动失败（超时）")


def _stop_via_mailbox() -> bool:
    """通过共享内存信箱发送 stop 请求（优雅停止）

    Returns:
        True 表示守护进程已响应停止。
    """
    token = read_auth_token() or ""
    seq = int(time.time() * 1000) % 100000
    req_name, resp_name = make_channel_names(os.getpid(), seq)

    req_shm = open_shm(req_name, REQ_SHM_SIZE)
    resp_shm = open_shm(resp_name, RESP_SHM_SIZE)
    if req_shm is None or resp_shm is None:
        close_shm(req_shm)
        close_shm(resp_shm)
        return False

    mailbox = Mailbox()
    slot = None
    try:
        write_message(req_shm, {"type": "stop"}, REQ_SHM_SIZE - _DATA_BODY_OFF,
                      truncated_marker=False)
        slot = mailbox.acquire_slot(os.getpid(), req_name, resp_name, token, seq)
        if slot is None:
            _safe_print("[pty-agent] 停止失败: 请求信箱已满")
            return False
        if not mailbox.wait_done(slot, STOP_TIMEOUT):
            _safe_print("[pty-agent] 停止守护进程失败（超时）")
            return False
        resp = read_message(resp_shm)
        return resp is not None and resp.get("type") == "ok"
    finally:
        if slot is not None:
            mailbox.release_slot(slot)
        close_shm(req_shm)
        close_shm(resp_shm)


def stop_daemon():
    """停止守护进程

    依次尝试：共享内存 stop 请求 → 强制 kill PID。
    """
    pid = _find_daemon_pid()
    if pid is None:
        _safe_print("[pty-agent] 守护进程未运行")
        _cleanup_shm_resources()
        return

    stopped = False
    try:
        stopped = _stop_via_mailbox()
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

    _cleanup_shm_resources()

    if stopped:
        _safe_print("[pty-agent] 守护进程已停止")


# ============================================================
#  守护进程入口
# ============================================================


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
    for name in ("pty-client", "pty-protocol", "pty-factory"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(fh)
        logger.setLevel(level)
        logger.propagate = False


def _hide_console_window():
    """隐藏当前进程的控制台窗口（Windows）"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


def _setup_logging():
    """配置日志：仅文件输出（UTF-8），无控制台输出"""
    level_name = DAEMON_LOG_LEVEL
    if level_name is None:
        for name in ("pty-daemon", "pty-session", "pty-subprocess", "pty-windows-error",
                     "pty-job", "pty-gui", "pty-factory", "pty-protocol",
                     "pty-windows", "pty-unix"):
            logger = logging.getLogger(name)
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.setLevel(logging.WARNING)
            logger.propagate = False
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "daemon.log")
    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setFormatter(logging.Formatter(
        "[pty-agent:daemon] %(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    level = getattr(logging, level_name.upper(), logging.DEBUG)
    for name in ("pty-daemon", "pty-session", "pty-subprocess", "pty-windows-error",
                 "pty-job", "pty-gui", "pty-factory", "pty-protocol",
                 "pty-windows", "pty-unix"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(fh)
        logger.setLevel(level)
        logger.propagate = False


def main():
    """守护进程入口

    通过共享内存发布 PID + 状态 + 心跳，启动信箱轮询服务器。
    无端口参数、无 socket。
    """
    _hide_console_window()
    _setup_logging()
    _logger.info("=== 守护进程启动 ===")

    _logger.info("PID: %s", os.getpid())

    try:
        from ..pty.subprocess import format_shell_info
        _logger.info(format_shell_info())
    except Exception:
        pass

    from .server import DaemonServer

    server = DaemonServer()
    try:
        server.run()
    except OSError as e:
        _logger.error("守护进程启动失败: %s", e)
        _safe_print(f"[pty-agent] 守护进程启动失败: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        _logger.info("收到键盘中断，关闭守护进程...")
    finally:
        _cleanup_shm_resources()
        server.stop()
