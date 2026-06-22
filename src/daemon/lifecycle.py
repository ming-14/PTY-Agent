"""守护进程层 — 生命周期管理

提供守护进程的启动/停止/检测函数和入口 main()。
端口动态分配：每次启动随机选取未被占用的端口，通过共享内存传递到客户端。
"""

import os
import sys
import time
import socket
import logging
import subprocess

from ..config import (
    DAEMON_HOST,
    DEFAULT_DAEMON_PORT,
    DATA_DIR,
    LOG_DIR,
    DAEMON_LOG_LEVEL,
    CLIENT_LOG_LEVEL,
    DAEMON_START_TIMEOUT,
    PING_TIMEOUT,
    STOP_TIMEOUT,
    IS_WINDOWS,
)
from ..session.shm_utils import read_port_from_shm, read_auth_token, cleanup_port_shm
from ..protocol.message import Message

_logger = logging.getLogger("pty-daemon")

def _safe_print(text: str):
    """安全打印中文：始终以 UTF-8 字节写入 stdout"""
    try:
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


def _find_free_port() -> int:
    """查找一个随机可用的 TCP 端口

    Returns:
        操作系统随机分配的可用端口号。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((DAEMON_HOST, 0))
        return s.getsockname()[1]


def _cleanup_port():
    """清理共享内存残留（委托给 config.py）"""
    cleanup_port_shm()


# ============================================================
#  生命周期函数
# ============================================================


def is_running() -> bool:
    """检查守护进程是否正在运行（ping-pong 探测）

    Returns:
        True 表示守护进程在运行。
    """
    port = read_port_from_shm()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(PING_TIMEOUT)
        sock.connect((DAEMON_HOST, port))
        Message.send(sock, {"type": "ping"})
        resp = Message.recv(sock)
        sock.close()
        return resp is not None and resp.get("type") == "pong"
    except (socket.error, ConnectionRefusedError, OSError):
        # 共享内存端口连接失败 → 守护进程已不在，清理旧共享内存
        _cleanup_port()
        return False


def start_daemon():
    """启动守护进程（以子进程方式）

    自动分配一个随机端口，通过共享内存传递给客户端。
    Windows: DETACHED_PROCESS 创建独立子进程。
    Unix:    双 fork 彻底守护化。
    注意：子进程的 stderr 重定向到日志文件，方便排查启动失败原因。
    """
    if is_running():
        _safe_print("[pty-agent] 守护进程已在运行中")
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    port = _find_free_port()

    # 子进程 stderr 重定向到日志文件（而非 DEVNULL），便于排查崩溃原因
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "daemon.log")

    # 计算项目根目录，确保 -m src.daemon 能找到模块
    # __file__ = .../src/daemon/lifecycle.py → 向上 3 层 = 项目根目录（src 的父目录）
    # 无论从哪个目录调用，子进程都在正确的目录下查找 src 包
    src_parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    if IS_WINDOWS:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        with open(log_file, "a", encoding="utf-8") as err_log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "src.daemon", "--port", str(port)],
                close_fds=True,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=err_log,
                cwd=src_parent,  # 重点：确保模块可被 import
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
        # Unix 下用 PYTHONPATH 确保能找到模块
        env = os.environ.copy()
        env["PYTHONPATH"] = src_parent + os.pathsep + env.get("PYTHONPATH", "")
        sys.argv = ["src.daemon", "--port", str(port)]

    # 等待守护进程就绪
    for _ in range(int(DAEMON_START_TIMEOUT / 0.3) + 1):
        if is_running():
            actual_port = read_port_from_shm()
            _safe_print(f"[pty-agent] 守护进程已启动 (端口 {actual_port})")
            _print_shell_info()
            return
        time.sleep(0.3)

    _safe_print(
        f"[pty-agent] 守护进程启动失败（超时），"
        f"端口 {port} 可能已被占用",
    )


def stop_daemon():
    """停止守护进程"""
    port = read_port_from_shm()
    if not is_running():
        _safe_print("[pty-agent] 守护进程未运行")
        return

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(STOP_TIMEOUT)
        sock.connect((DAEMON_HOST, port))
        Message.send(sock, {"type": "stop", "token": read_auth_token() or ""})
        resp = Message.recv(sock)
        sock.close()
        if resp and resp.get("type") == "ok":
            _safe_print("[pty-agent] 守护进程已停止")
        else:
            _safe_print("[pty-agent] 停止守护进程失败")
    except Exception as e:
        _safe_print(f"[pty-agent] 停止守护进程失败: {e}")
    finally:
        _cleanup_port()


# ============================================================
#  守护进程入口
# ============================================================


def setup_client_logging():
    """前台模式日志配置：写入 <程序根>/logs/client.log

    为 pty-client 等前台相关 logger 配置文件输出。
    CLIENT_LOG_LEVEL 设为 None 则不配置日志。
    """
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


def _setup_logging():
    """配置日志：仅文件输出（UTF-8），无控制台输出

    避免 Chinese UTF-8 日志在 GBK 控制台上显示为乱码。
    守护进程的 stderr 可能继承自父进程，不设 StreamHandler。
    DAEMON_LOG_LEVEL 设为 None 则不配置日志。
    """
    if DAEMON_LOG_LEVEL is None:
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "daemon.log")
    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setFormatter(logging.Formatter(
        "[pty-agent:daemon] %(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    level = getattr(logging, DAEMON_LOG_LEVEL.upper(), logging.DEBUG)
    # 为所有 pty-* logger 添加文件输出
    for name in ("pty-daemon", "pty-session", "pty-subprocess", "pty-windows-error",
                 "pty-job", "pty-gui", "pty-factory", "pty-protocol",
                 "pty-windows", "pty-unix"):
        logger = logging.getLogger(name)
        logger.handlers.clear()  # 清除可能继承的 StreamHandler
        logger.addHandler(fh)
        logger.setLevel(level)
        logger.propagate = False  # 禁止传播到 root logger


def main():
    """守护进程入口

    支持 --port <N> 参数指定监听端口（由 start_daemon 传入）。
    通过共享内存发布端口号，启动 TCP 服务器。
    服务器退出后清理资源。
    """
    _setup_logging()
    _logger.info("=== 守护进程启动 ===")

    # 解析端口参数
    port = DEFAULT_DAEMON_PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            try:
                port = int(sys.argv[idx + 1])
            except ValueError:
                pass

    _logger.info("PID: %s, port: %s", os.getpid(), port)

    try:
        from ..pty.subprocess import format_shell_info
        _logger.info(format_shell_info())
    except Exception:
        pass

    from .server import DaemonServer

    server = DaemonServer(port=port)
    try:
        server.run()
    except OSError as e:
        _logger.error("守护进程启动失败: %s", e)
        _safe_print(f"[pty-agent] 守护进程启动失败: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        _logger.info("收到键盘中断，关闭守护进程...")
    finally:
        _cleanup_port()
        server.stop()
