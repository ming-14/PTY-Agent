"""守护进程侧 — 入口与进程上下文

只负责 daemon 进程的入口与进程上下文（日志配置 / 控制台处理 / 单实例获取）。
守护进程的启动/停止/探测（start_daemon / stop_daemon / is_running / 端口发现）
属客户端控制能力，位于 src/client/daemonctl。
"""

import argparse
import os
import sys

from ..config.daemon import SINGLE_INSTANCE, TOKEN_ENABLED, LOG_DIR, DAEMON_LOG_LEVEL, WEB_LOG_LEVEL
from ..ipc.shm import cleanup_all_shm
from ..ipc.single_instance import SingleInstanceLock
from ..logging import get_logger, setup_daemon_logging, shutdown

_logger = get_logger("pty-daemon")


def _parse_args(argv):
    """解析 daemon 入口参数

    --foreground / PTY_AGENT_FOREGROUND=1：前台运行，不脱离终端，日志同时
    输出到 stderr。供 s6/systemd 等服务监督器以 longrun 方式管理：监督器
    持有前台进程，SIGTERM 优雅退出，异常退出由监督器重启（容器内 daemon
    被监督器误杀的根本原因是双 fork 守护化后进程脱离监督，故需前台模式）。

    --survive / PTY_AGENT_SURVIVE=1：生存模式，运行期间拦截忽略所有结束
    进程的信号（SIGTERM/SIGHUP/SIGINT/SIGQUIT）与 stop 协议消息，仅
    SIGKILL 可终止（stop --force 仍可用）。与 foreground 可组合。

    Returns:
        (foreground, survive) 布尔元组。
    """

    def _env_flag(name: str) -> bool:
        return os.environ.get(name, "").lower() in ("1", "true", "yes")

    parser = argparse.ArgumentParser(prog="pty-agent-daemon", add_help=True)
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="前台运行（供 s6/systemd 等服务监督器管理，日志输出到 stderr）",
    )
    parser.add_argument(
        "--survive",
        action="store_true",
        help="生存模式：忽略所有结束进程的信号与 stop 消息，仅 SIGKILL 可终止",
    )
    args, _ = parser.parse_known_args(argv)
    return (
        args.foreground or _env_flag("PTY_AGENT_FOREGROUND"),
        args.survive or _env_flag("PTY_AGENT_SURVIVE"),
    )


def _safe_print(text: str):
    """安全打印：始终输出 JSON 格式到 stdout"""
    import json

    try:
        msg = json.dumps({"type": "info", "message": text}, ensure_ascii=False)
        sys.stdout.buffer.write(msg.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
    except Exception:
        pass


# ============================================================
#  日志配置与进程上下文
# ============================================================


def _hide_console_window():
    """隐藏当前进程的控制台窗口（Windows）

    venv python.exe 启动系统 python.exe 时，系统 python 会创建自己的控制台窗口，
    CREATE_NO_WINDOW 和 STARTUPINFO 对此无效。在守护进程入口主动调用 FreeConsole
    彻底脱离控制台，使窗口消失。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


def _ignore_console_ctrl():
    """注册 Windows 控制台 Ctrl+C 忽略处理器

    send_signal 使用 AllocConsole + GenerateConsoleCtrlEvent 向子进程发送 SIGINT，
    这会同时触发守护进程自身的 Ctrl+C 处理。注册忽略处理器防止守护进程被误杀。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes as W

        HANDLER_ROUTINE = ctypes.WINFUNCTYPE(W.BOOL, W.DWORD)
        _ctrl_handler = HANDLER_ROUTINE(lambda ctrl_type: True)
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleCtrlHandler(_ctrl_handler, True)
        _CTRL_HANDLER = _ctrl_handler  # prevent GC
    except Exception:
        pass


def _setup_logging(console: bool = False):
    """配置日志系统：按模块分组写独立日志文件 + 异步队列 + 归档线程

    Args:
        console: 同时向 stderr 输出（前台运行 / 服务监督器场景）。

    委托给 src/logging 子包的 setup_daemon_logging()。
    """
    files = setup_daemon_logging(console=console)
    for group, path in files.items():
        _safe_print(f"[pty-agent] {group} log: {path}")


# ============================================================
#  守护进程入口
# ============================================================


def _install_survive_signal_guards():
    """生存模式：入口即注册信号忽略处理器（仅 SIGKILL 可终止）

    必须在 server.run() 之前安装——daemon 启动（插件/web 初始化）耗时可达
    数秒，期间到达的 SIGTERM 会走默认处理器直接杀死进程。此处入口即注册，
    覆盖整个启动窗口；server.run() 在生存模式下不再注册任何信号（见
    DaemonServer.run），避免与入口处理器重复。
    """
    if sys.platform == "win32":
        return
    import signal as _signal

    def _ignore(signum, frame):
        _logger.warning(
            "生存模式忽略信号 %s (%s)", signum, _signal.Signals(signum).name
        )

    for sig in (_signal.SIGTERM, _signal.SIGHUP, _signal.SIGINT, _signal.SIGQUIT):
        try:
            _signal.signal(sig, _ignore)
        except (OSError, ValueError):
            pass
    _logger.warning("生存模式：已忽略 SIGTERM/SIGHUP/SIGINT/SIGQUIT，仅 SIGKILL 可终止")


def main(argv=None):
    """守护进程入口

    监听位置完全由 daemon.toml [listener] 段控制。
    入口处获取 Windows 命名互斥 / Unix flock 单实例锁，失败则直接退出。

    支持前台运行模式（--foreground / PTY_AGENT_FOREGROUND=1）：
    供 s6/systemd 等服务监督器以 longrun 方式管理，日志同时输出到 stderr。
    支持生存模式（--survive / PTY_AGENT_SURVIVE=1）：忽略所有结束进程的
    信号与 stop 消息，仅 SIGKILL 可终止。
    """
    foreground, survive = _parse_args(argv)
    # 终端颜色语义：PTY 会话经 ConPTY + 前端 xterm 渲染 ANSI 颜色——剥离
    # NO_COLOR（尊重该变量的程序会禁用颜色输出）。须在入口剥离：PTY 后端
    #（Rust 侧）的环境块 = 进程环境 + env 覆盖，Python 侧无法删除基础
    # 环境变量——os.environ.pop 只改 Python dict 不修改进程环境块，
    # 须用 os.unsetenv 真正删除（Rust std::env 才能看到）。
    os.unsetenv("NO_COLOR")
    _hide_console_window()
    _setup_logging(console=foreground)
    _ignore_console_ctrl()
    if survive:
        # 入口即注册信号防护：覆盖 server.run() 之前的整个启动窗口
        # （插件/web 初始化期间到达的 SIGTERM 会被忽略而非杀死进程）
        _install_survive_signal_guards()
    if foreground:
        _logger.info("守护进程前台模式启动（PID=%d），日志输出到 stderr+文件", os.getpid())
    if survive:
        _logger.warning(
            "守护进程生存模式启动（PID=%d）：忽略 SIGTERM/SIGHUP/SIGINT/SIGQUIT "
            "与 stop 协议消息，仅 SIGKILL 可终止", os.getpid()
        )
    _logger.info("=== 守护进程启动 ===")
    _logger.info(
        "PID=%s, Python=%s, platform=%s",
        os.getpid(),
        sys.version.split()[0],
        sys.platform,
    )
    _logger.info(
        "LOG_DIR=%s, DAEMON_LOG_LEVEL=%s, WEB_LOG_LEVEL=%s",
        LOG_DIR,
        DAEMON_LOG_LEVEL,
        WEB_LOG_LEVEL,
    )

    single_lock = SingleInstanceLock()
    # 单实例锁强制保留条件：token 监听器启用时 CLI 依赖互斥锁做存活发现与自动启动
    if SINGLE_INSTANCE or TOKEN_ENABLED:
        if not SINGLE_INSTANCE:
            _logger.warning(
                "SINGLE_INSTANCE=false 但 token 监听器启用（CLI 依赖互斥锁做发现），"
                "强制保留单实例互斥锁"
            )
        if not single_lock.try_acquire():
            _logger.warning("守护进程已在运行，当前实例退出")
            _safe_print("[pty-agent] Daemon already running")
            sys.exit(0)
        _logger.info("已获取单实例锁")
    else:
        # 仅 basic/tls 监听器：跳过单实例锁，允许同机多实例并存（各实例独立端口配置）
        _logger.warning(
            "SINGLE_INSTANCE=false 且无 token 监听器，跳过单实例互斥锁（允许多实例并存）"
        )

    try:
        from ..common.shells import format_shell_info

        _logger.info("Shell info: %s", format_shell_info())
    except Exception:
        _logger.debug("无法获取 shell 信息", exc_info=True)

    from .server import DaemonServer

    server = DaemonServer(survive=survive)
    _logger.info("DaemonServer 实例已创建，准备运行")
    try:
        server.run()
    except OSError as e:
        _logger.error("守护进程启动失败: %s", e)
        _safe_print(f"[pty-agent] Daemon start failed: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        _logger.info("收到键盘中断，关闭守护进程...")
    except Exception:
        _logger.exception("守护进程主循环异常")
    finally:
        _logger.info("守护进程进入清理阶段")
        cleanup_all_shm()
        try:
            server.stop()
        except Exception:
            _logger.exception("守护进程清理异常")
        single_lock.release()
        shutdown()
        _logger.info("守护进程已退出")
