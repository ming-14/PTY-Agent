"""Windows 控制台信号辅助 — Ctrl+C 发送与守护进程控制台处理器

仅 Windows 使用；Unix 平台导入时零开销（ctypes 相关代码在 IS_WINDOWS 分支内）。
守护进程忽略 CTRL_C_EVENT 的控制台处理器在模块导入时安装；
AttachConsole + GenerateConsoleCtrlEvent 的串行化锁在此集中管理。
"""

import threading

from ..config.common import IS_WINDOWS
from ..logging import get_logger

_logger = get_logger("pty-session")

# Windows 下发送 Ctrl+C 需要 AttachConsole，而一个线程同时只能附加到一个控制台，
# 多会话并发发送信号时必须串行化，否则会互相抢占控制台归属
console_lock = threading.Lock() if IS_WINDOWS else None

if IS_WINDOWS:
    import ctypes as _ctypes
    from ctypes import wintypes as _W

    # 守护进程忽略 CTRL_C_EVENT 的控制台处理器。
    # GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0) 会发给控制台上所有进程，
    # 守护进程自己也会收到，必须忽略以免被中断。
    # 注意：HANDLER_ROUTINE 回调必须保持全局引用，防止 GC 后崩溃。
    _HANDLER_ROUTINE = _ctypes.WINFUNCTYPE(_W.BOOL, _W.DWORD)

    def _daemon_ctrl_handler(ctrl_type):
        # CTRL_C_EVENT = 0, CTRL_BREAK_EVENT = 1
        # 返回 True 表示已处理，阻止后续处理器（含 Python KeyboardInterrupt）被调用
        return ctrl_type in (0, 1)

    _daemon_ctrl_handler_ref = _HANDLER_ROUTINE(_daemon_ctrl_handler)
    try:
        _ctypes.WinDLL("kernel32", use_last_error=True).SetConsoleCtrlHandler(
            _daemon_ctrl_handler_ref, True
        )
    except Exception as e:
        _logger.warning("SetConsoleCtrlHandler 安装失败: %s", e)


def send_ctrl_c(pty, pid, session_id) -> None:
    """Windows 下向子进程发送 SIGINT（Ctrl+C）

    子进程以 CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP 创建，
    拥有独立控制台和进程组。通过 AttachConsole 附加到子进程控制台后，
    用 GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0) 向该控制台所有进程发送 Ctrl+C。

    注意：CTRL_C_EVENT 不能针对进程组发送（dwProcessGroupId != 0 时无效），
    必须用 dwProcessGroupId=0 广播。守护进程已安装全局处理器忽略 CTRL_C_EVENT，
    所以只有子进程及其后代会收到信号。

    AttachConsole 会改变当前线程的控制台归属，必须加锁串行化，
    避免多会话同时发送信号时互相干扰。失败时回退到写 \\x03 到 stdin。

    Args:
        pty:        会话的 PTY 实例（失败回退时写入 \\x03）。
        pid:        子进程 PID。
        session_id: 会话 ID（仅用于日志）。
    """
    if not IS_WINDOWS:
        return
    import ctypes
    from ctypes import wintypes as W

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.FreeConsole.argtypes = []
    kernel32.FreeConsole.restype = W.BOOL
    kernel32.AttachConsole.argtypes = [W.DWORD]
    kernel32.AttachConsole.restype = W.BOOL
    kernel32.GenerateConsoleCtrlEvent.argtypes = [W.DWORD, W.DWORD]
    kernel32.GenerateConsoleCtrlEvent.restype = W.BOOL

    CTRL_C_EVENT = 0

    def _fallback_write_ctrl_c():
        """AttachConsole 失败时的兜底：写 \\x03 到 stdin（非真实信号）"""
        try:
            pty.write(b"\x03")
            _logger.info(
                "send_signal: sid=%r \\x03 stdin pid=%d (fallback)", session_id, pid
            )
        except Exception as e:
            _logger.warning(
                "send_signal all methods failed: sid=%r pid=%d err=%s",
                session_id,
                pid,
                e,
            )

    with console_lock:
        # 先脱离当前控制台（守护进程可能没有控制台，失败也无妨）
        kernel32.FreeConsole()
        try:
            # 附加到子进程的独立控制台
            if not kernel32.AttachConsole(pid):
                err = ctypes.get_last_error()
                _logger.debug(
                    "send_signal: AttachConsole failed pid=%d err=%d, fallback to \\x03",
                    pid,
                    err,
                )
                _fallback_write_ctrl_c()
                return
            try:
                # CTRL_C_EVENT 不能针对进程组发送（pid != 0 时无效），
                # 必须用 dwProcessGroupId=0 广播给控制台上所有进程；
                # 守护进程已安装全局处理器忽略它，只有子进程会收到
                if kernel32.GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0):
                    _logger.info(
                        "send_signal: sid=%r SIGINT pid=%d (AttachConsole+CtrlEvent(0))",
                        session_id,
                        pid,
                    )
                else:
                    err = ctypes.get_last_error()
                    _logger.warning(
                        "send_signal: GenerateConsoleCtrlEvent failed err=%d, fallback to \\x03",
                        err,
                    )
                    _fallback_write_ctrl_c()
            finally:
                # 无论成功与否，都脱离子进程控制台，避免影响后续 AttachConsole
                kernel32.FreeConsole()
        except Exception as e:
            _logger.warning(
                "send_signal: sid=%r AttachConsole path failed pid=%d err=%s, fallback to \\x03",
                session_id,
                pid,
                e,
            )
            _fallback_write_ctrl_c()
