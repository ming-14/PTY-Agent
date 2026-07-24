"""强制修改子进程的 CONIN$ 模式为 0x298 (VT_INPUT + MOUSE_INPUT)

测试方案：ConPTY 下 conhost 在 VT_INPUT 模式下不翻译 SGR 鼠标序列，
因为子进程没有启用 MOUSE_INPUT。强制把 console mode 改为 0x298，
让 conhost 同时解析 VT 序列和翻译 SGR 鼠标序列为 mouseEvent。

用法：python force_mouse_mode.py <pid> [mode]
  pid: 子进程 PID
  mode: 0x298 (默认) 或 0x98 或 0x288
"""
import sys
import ctypes
import os
from ctypes import wintypes as W

# 日志写到文件，避免 FreeConsole 后 stdout 丢失
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "force_mouse_mode.log")
_log_fh = open(LOG_FILE, "w", encoding="utf-8")

def log(msg):
    _log_fh.write(msg + "\n")
    _log_fh.flush()
    # 不调用 print()：FreeConsole+AttachConsole 后 stdout 缓存的 console handle
    # 可能指向目标子进程的控制台 (gdu PTY)，会把日志写入 gdu 屏幕污染显示

ENABLE_MOUSE_INPUT = 0x0010
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_WINDOW_INPUT = 0x0008
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

ATTACH_PARENT_PROCESS = -1

kernel32 = ctypes.windll.kernel32

# 设置返回值类型
kernel32.AttachConsole.restype = W.BOOL
kernel32.FreeConsole.restype = W.BOOL
kernel32.GetConsoleMode.restype = W.BOOL
kernel32.SetConsoleMode.restype = W.BOOL
kernel32.CloseHandle.restype = W.BOOL
# CreateFileW 返回 HANDLE (void*)，需要设为 c_void_p 避免符号扩展问题
kernel32.CreateFileW.restype = ctypes.c_void_p

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

def attach_and_set_mode(pid: int, mode: int) -> bool:
    """附加到子进程的控制台，设置 CONIN$ 模式"""
    # FreeConsole（脱离当前控制台）
    ok = kernel32.FreeConsole()
    log(f"FreeConsole: ok={ok} err={ctypes.get_last_error()}")

    # AttachConsole 到目标进程
    ok = kernel32.AttachConsole(pid)
    err = ctypes.get_last_error()
    log(f"AttachConsole({pid}): ok={ok} err={err}")
    if not ok:
        log(f"AttachConsole({pid}) failed err={err}")
        return False

    try:
        # 打开 CONIN$
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        FILE_SHARE_READ = 1
        FILE_SHARE_WRITE = 2
        h = kernel32.CreateFileW(
            "CONIN$",
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        err = ctypes.get_last_error()
        log(f"CreateFileW(CONIN$): h={h:#x} err={err}")
        if h == INVALID_HANDLE_VALUE or h == 0 or h is None:
            log(f"CreateFileW(CONIN$) failed err={err}")
            return False

        try:
            # 读取当前模式
            cur_mode = W.DWORD(0)
            if not kernel32.GetConsoleMode(h, ctypes.byref(cur_mode)):
                err = ctypes.get_last_error()
                log(f"GetConsoleMode failed err={err}")
                return False
            log(f"current CONIN$ mode: 0x{cur_mode.value:x}")

            # 设置新模式
            new_mode = W.DWORD(mode)
            if not kernel32.SetConsoleMode(h, new_mode):
                err = ctypes.get_last_error()
                log(f"SetConsoleMode(0x{mode:x}) failed err={err}")
                return False

            # 验证
            verify = W.DWORD(0)
            if kernel32.GetConsoleMode(h, ctypes.byref(verify)):
                log(f"verify CONIN$ mode: 0x{verify.value:x} (target=0x{mode:x})")
                if verify.value == mode:
                    log(f"SUCCESS: CONIN$ mode changed 0x{cur_mode.value:x} -> 0x{verify.value:x}")
                    return True
                else:
                    log(f"PARTIAL: target=0x{mode:x} actual=0x{verify.value:x}")
                    return True
            else:
                log(f"verify GetConsoleMode failed")
                return True
        finally:
            kernel32.CloseHandle(h)
    finally:
        # 脱离子进程控制台
        kernel32.FreeConsole()


def main():
    if len(sys.argv) < 2:
        log(f"Usage: {sys.argv[0]} <pid> [mode_hex]")
        sys.exit(1)

    pid = int(sys.argv[1])
    mode = 0x298  # 默认 VT_INPUT + MOUSE_INPUT
    if len(sys.argv) >= 3:
        mode = int(sys.argv[2], 16)

    log(f"force_mouse_mode: pid={pid} target_mode=0x{mode:x}")
    # 解释模式位
    parts = []
    if mode & ENABLE_VIRTUAL_TERMINAL_INPUT:
        parts.append("VT_INPUT")
    if mode & ENABLE_MOUSE_INPUT:
        parts.append("MOUSE_INPUT")
    if mode & ENABLE_WINDOW_INPUT:
        parts.append("WINDOW_INPUT")
    if mode & ENABLE_EXTENDED_FLAGS:
        parts.append("EXTENDED_FLAGS")
    log(f"mode bits: {' | '.join(parts)}")

    ok = attach_and_set_mode(pid, mode)

    # 重新附加到父进程（daemon）的控制台
    kernel32.FreeConsole()
    kernel32.AttachConsole(ATTACH_PARENT_PROCESS)

    _log_fh.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
