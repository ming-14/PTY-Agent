"""ConPTY 输入模式初始化 — 启用 ENABLE_VIRTUAL_TERMINAL_INPUT

ConPTY 创建后 conhost 输入侧默认不解析 VT 输入（ENABLE_VIRTUAL_TERMINAL_INPUT
未置位），向输入管道写入的 0x03 只被当作"取消当前行读取"而不会产生
CTRL_C_EVENT，导致 ConPTY 会话内 Ctrl+C 无法中断进程。

启用 VT_INPUT 后 conhost 输入侧进入 VT 解析：
  - 0x03 解析为 Ctrl+C 键事件，产生 CTRL_C_EVENT
  - 注入的 MOUSE_EVENT_RECORD 由 conhost 翻译为 SGR-1006 字节流送子进程 stdin
    （前提：conhost 自己启用 ?1006 鼠标模式，由 on_ready 回调发送 DECSET）

注意：conhost 服务器完成控制台注册需要约 1-2 秒，过早 AttachConsole 会
返回 ERROR_GEN_FAILURE(31)，因此提供带重试的后台初始化。
"""

import ctypes
import logging
import threading
import time
from ctypes import wintypes as W

from .win32_api import (
    _AttachConsole,
    _CloseHandle,
    _CreateFileW,
    _FreeConsole,
    _GetConsoleMode,
    _SetConsoleMode,
    ENABLE_VIRTUAL_TERMINAL_INPUT,
)

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 1
_FILE_SHARE_WRITE = 2
_OPEN_EXISTING = 3

_logger = logging.getLogger("pty-vt-input")


def enable_vt_input(pid, lock: threading.Lock, logger=_logger,
                    *, retries: int = 12, delay: float = 0.3,
                    on_ready=None) -> bool:
    """启用子进程控制台输入的 VT 解析模式

    AttachConsole 是进程级操作，必须在调用方持有的控制台附加锁（lock）保护下
    执行，避免与鼠标/按键注入等操作互相 detached。

    Args:
        pid: 子进程 PID（ConPTY 会话内的客户进程）。
        lock: 控制台附加串行化锁（与注入路径共用）。
        retries/delay: conhost 就绪前 AttachConsole 返回 ERROR_GEN_FAILURE(31)，
                       按 retries × delay 重试。
        on_ready: VT 输入解析开启成功后的回调（无参）。用于执行依赖 VT
                  解析的初始化（如向 conhost 发送 ?1002h/?1006h 启用鼠标
                  SGR-1006 翻译）。

    Returns:
        是否成功（成功一次即返回；全部失败返回 False）。
    """
    with lock:
        for attempt in range(retries):
            _FreeConsole()
            if not _AttachConsole(pid):
                err = ctypes.get_last_error()
                logger.debug("vt_input: pid=%d 尝试 %d/%d attach 失败 err=%d",
                             pid, attempt + 1, retries, err)
                time.sleep(delay)
                continue
            h = W.HANDLE()
            try:
                h = _CreateFileW("CONIN$", _GENERIC_READ | _GENERIC_WRITE,
                                 _FILE_SHARE_READ | _FILE_SHARE_WRITE,
                                 None, _OPEN_EXISTING, 0, None)
                if not h or h == W.HANDLE(-1):
                    logger.debug("vt_input: pid=%d CONIN$ 打开失败 err=%d",
                                 pid, ctypes.get_last_error())
                    continue
                mode = W.DWORD()
                if not _GetConsoleMode(h, ctypes.byref(mode)):
                    logger.debug("vt_input: pid=%d GetConsoleMode 失败 err=%d",
                                 pid, ctypes.get_last_error())
                    continue
                if mode.value & ENABLE_VIRTUAL_TERMINAL_INPUT:
                    # 已开启（幂等路径），验证成功即返回
                    logger.debug("vt_input: pid=%d 已是 VT_INPUT 模式 0x%x",
                                 pid, mode.value)
                    if on_ready is not None:
                        on_ready()
                    return True
                new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_INPUT
                ok = _SetConsoleMode(h, new_mode)
                if not ok:
                    logger.warning("vt_input: pid=%d SetConsoleMode 失败 err=%d",
                                   pid, ctypes.get_last_error())
                    continue
                verify = W.DWORD()
                _GetConsoleMode(h, ctypes.byref(verify))
                logger.info("vt_input: pid=%d mode=0x%x -> 0x%x (att=%d)",
                            pid, mode.value, verify.value, attempt)
                if on_ready is not None:
                    on_ready()
                return True
            finally:
                if h and h != W.HANDLE(-1):
                    _CloseHandle(h)
                _FreeConsole()
    logger.warning("vt_input: pid=%d 初始化失败（%d 次尝试）", pid, retries)
    return False


def spawn_vt_input_init(pid, lock: threading.Lock, logger=_logger, on_ready=None):
    """后台线程启动 VT 输入初始化（不阻塞 PTY 创建流程）

    conhost 完成控制台注册需 1-2 秒，紧跟创建即时调用会失败；
    daemon 线程内带重试执行 enable_vt_input，异常仅记录不抛出。
    on_ready 在初始化成功后执行（同一线程，保证顺序）。
    """
    def _worker():
        try:
            enable_vt_input(pid, lock, logger, on_ready=on_ready)
        except Exception:
            logger.exception("vt_input: pid=%d 后台初始化异常", pid)

    threading.Thread(target=_worker, name="vt-input-init", daemon=True).start()