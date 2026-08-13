"""WindowsConPTY — 基于 kernel32.CreatePseudoConsole API 的 ConPTY 实现"""

import logging
import os
import subprocess
import ctypes
import threading
from contextlib import contextmanager
from ctypes import wintypes as W
from typing import Optional, List

from ..base import PseudoTerminal
from ...config.common import DEFAULT_COLS, DEFAULT_ROWS

_logger = logging.getLogger("pty-windows")
from .win32_api import (
    K,
    _CloseHandle,
    _CreateFileW,
    _InitAttrList,
    _UpdateAttr,
    _DeleteAttrList,
    _CreateProcess,
    _HPCON,
    _SIE,
    _PI,
    _AttachConsole,
    _FreeConsole,
    _GetConsoleMode,
    _SetConsoleMode,
    _WriteConsoleInputW,
    _MapVirtualKeyW,
    STD_INPUT_HANDLE,
    ENABLE_MOUSE_INPUT,
    ENABLE_EXTENDED_FLAGS,
    ENABLE_QUICK_EDIT_MODE,
    ENABLE_LINE_INPUT,
    ENABLE_ECHO_INPUT,
    ENABLE_PROCESSED_INPUT,
    ENABLE_VIRTUAL_TERMINAL_INPUT,
    MOUSE_EVENT,
    MOUSE_MOVED,
    DOUBLE_CLICK,
    MOUSE_WHEELED,
    MOUSE_HWHEELED,
    FROM_LEFT_1ST_BUTTON_PRESSED,
    RIGHTMOST_BUTTON_PRESSED,
    FROM_LEFT_2ND_BUTTON_PRESSED,
    WHEEL_DELTA,
    SHIFT_PRESSED,
    LEFT_ALT_PRESSED,
    LEFT_CTRL_PRESSED,
    _INPUT_RECORD,
    _MOUSE_EVENT_RECORD,
    KEY_EVENT,
    _KEY_EVENT_RECORD,
)
from ...process.windows.api import _GetExitCodeProcess
from ...process.win32_error import STILL_ACTIVE
from ...process.base import ProcessTreeTracker

# AttachConsole/FreeConsole 是进程级操作，多个线程同时调用会互相 detached，
# 因此所有需要附加到子进程控制台的操作都通过此锁串行化。
_CONSOLE_ATTACH_LOCK = threading.Lock()

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 1
_FILE_SHARE_WRITE = 2
_OPEN_EXISTING = 3


@contextmanager
def _attach_conin(pid, caller: str = ""):
    """上下文管理器：AttachConsole(pid) → CreateFileW("CONIN$") → yield 句柄 → 自动清理

    自动处理 FreeConsole 和 CloseHandle，调用方只需关注业务逻辑。
    返回 None 表示附加失败（AttachConsole 或 CreateFileW 失败）。
    """
    with _CONSOLE_ATTACH_LOCK:
        _FreeConsole()
        if not _AttachConsole(pid):
            err = ctypes.get_last_error()
            _logger.debug("%s: AttachConsole(%d) failed err=%d", caller, pid, err)
            yield None
            return
        try:
            hConIn = _CreateFileW(
                "CONIN$",
                _GENERIC_READ | _GENERIC_WRITE,
                _FILE_SHARE_READ | _FILE_SHARE_WRITE,
                None,
                _OPEN_EXISTING,
                0,
                None,
            )
            if not hConIn or hConIn == W.HANDLE(-1):
                err = ctypes.get_last_error()
                _logger.debug("%s: CreateFile CONIN$ failed err=%d", caller, err)
                yield None
            else:
                try:
                    yield hConIn
                finally:
                    _CloseHandle(hConIn)
        finally:
            _FreeConsole()


class WindowsPseudoTerminal(PseudoTerminal):
    """ConPTY — 基于 kernel32.CreatePseudoConsole API

    使用 CreatePseudoConsole + 双 CreatePipe 匿名管道。
    """

    def __init__(self, command, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS, env=None, cwd=None,
                 encoding: Optional[str] = None, tracker: Optional["ProcessTreeTracker"] = None):
        self._pty_h = None
        self._ph = None
        self._child_pid = None
        self._cols = cols
        self._rows = rows
        # Session 注入的进程树追踪器（spawn 成功后同一路径内 register_root，
        # 子进程自动继承 Job 归属，杜绝进程树逃逸）
        self._tracker = tracker

        if encoding:
            _logger.debug("WindowsPseudoTerminal: encoding=%s (ConPTY output is always UTF-8)", encoding)
        _logger.info("WindowsPseudoTerminal: creating pipes for cmd=%r", command)
        # ConPTY 句柄组件：双管道 + CreatePseudoConsole（COORD 按值传参），
        # 与 SandboxPty（沙箱外部传入 hpcon）共用同一实现
        from .conpty_handle import ConPtyHandle
        self._pty_h = ConPtyHandle(cols, rows)
        self._hpc = self._pty_h.hpc

        cmdline = subprocess.list2cmdline(command)
        cmdline_buf = ctypes.create_unicode_buffer(cmdline)
        attr_size = ctypes.c_size_t(0)
        _InitAttrList(None, 1, 0, ctypes.byref(attr_size))
        buf = ctypes.create_string_buffer(attr_size.value)
        if not _InitAttrList(buf, 1, 0, ctypes.byref(attr_size)):
            raise OSError("InitAttrList 失败")
        if not _UpdateAttr(
            buf, 0, 0x00020016,
            self._hpc, ctypes.sizeof(_HPCON), None, None,
        ):
            _DeleteAttrList(buf)
            raise OSError("UpdateAttr 失败")
        si = _SIE()
        si.StartupInfo.cb = ctypes.sizeof(_SIE)
        si.StartupInfo.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
        # hStdInput/Output/Error 保持 NULL — ConPTY 内核驱动自动分配控制台句柄
        si.lpAttributeList = ctypes.cast(buf, ctypes.c_void_p)
        _CREATE_UNICODE_ENVIRONMENT = 0x00080000
        _EXTENDED_STARTUPINFO_PRESENT = 0x00000400
        env_block = None
        env_dict = os.environ.copy()
        if isinstance(env, dict):
            env_dict.update(env)
        # ConPTY 输出始终是 UTF-8 VT 序列。
        # Python 3.6+ 默认使用 _WindowsConsoleIO（按 UTF-8 解码字节后 WriteConsoleW），
        # UTF-8 输出可直接被 ConPTY 正确透传为 UTF-8。
        # 仅当用户显式指定非 UTF-8 编码（如 gbk）时，启用传统字节模式：
        # Python 按指定编码输出字节 → 控制台按默认代码页（cp936）解码为 Unicode → ConPTY 输出 UTF-8。
        if encoding:
            enc_norm = encoding.lower().replace("-", "").replace("_", "")
            if enc_norm not in ("utf8", "utf"):
                env_dict.setdefault("PYTHONIOENCODING", encoding)
                env_dict.setdefault("PYTHONLEGACYWINDOWSSTDIO", "1")
        env_block = ctypes.create_unicode_buffer(
            "\0".join(f"{k}={v}" for k, v in env_dict.items()) + "\0\0",
        )
        pi = _PI()
        ok = _CreateProcess(
            None, cmdline_buf, None, None, False,
            _CREATE_UNICODE_ENVIRONMENT | _EXTENDED_STARTUPINFO_PRESENT,
            env_block, cwd,
            ctypes.byref(si.StartupInfo), ctypes.byref(pi),
        )
        _DeleteAttrList(buf)
        if not ok:
            err = ctypes.get_last_error()
            _logger.error("CreateProcessW 失败: err=%d", err)
            raise OSError(err, "CreateProcessW 失败")
        self._child_pid = pi.dwProcessId
        self._ph = pi.hProcess
        _logger.info("CreateProcessW OK pid=%d", self._child_pid)
        _CloseHandle(pi.hThread)
        # 同一代码路径内登记 root 到 tracker（AssignProcessToJobObject，
        # 子进程自动继承 Job 归属）
        if self._tracker:
            self._tracker.register_root(self._child_pid, pi.hProcess)

        # 关闭父进程中不再需要的可继承句柄副本
        # （CreatePseudoConsole 内部已复制了这些句柄给 conhost）
        self._pty_h.discard_inherited_ends()
        # 注意：不在此处关闭 _hpc（ClosePseudoConsole），
        # 因为 conhost 仍需要伪控制台存活。在 close() 中统一清理。

    def read(self, n: int = 65536) -> bytes:
        """阻塞读取输出（最多 n 字节）；EOF 返回 b"""""
        return self._pty_h.read(n)

    def drain(self, max_bytes: int = 65536) -> bytes:
        """排空管道输出缓冲区中当前所有就绪数据（基于 PeekNamedPipe 非阻塞检查）"""
        return self._pty_h.drain(max_bytes)

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        # 详细记录 SGR 鼠标序列的写入
        if b'\x1b[<' in data:
            _logger.info("write: %d bytes SGR_MOUSE data=%r", len(data), data[:200])
        else:
            _logger.debug("write: %d bytes", len(data))
        self._pty_h.write(data)

    def _cleanup(self, caller: str = ""):
        """统一清理：CancelIoEx → ClosePseudoConsole → 关闭管道 → 关闭进程句柄

        关闭顺序很关键（避免死锁）：
        1. CancelIoEx(_outR) → 取消 reader 线程挂起的 ReadFile
        2. 关闭伪控制台 → conhost 退出（进程树已由 Session 在 kill_tree 阶段终止）
        3. 关闭管道句柄 → reader 后续 read 也立即失败
        4. 关闭进程句柄

        注意：进程树终止（kill_tree）与 tracker 关闭由 Session 编排，
        顺序为 kill_tree → pty.close → tracker.close。
        """
        if self._pty_h is not None:
            self._pty_h.close()
        if self._ph:
            try:
                _CloseHandle(self._ph)
            except Exception:
                pass
            self._ph = None

    def resize(self, cols: int, rows: int):
        if self._pty_h is not None:
            self._pty_h.resize(cols, rows)

    def close(self):
        """关闭伪终端并清理资源（幂等：kill_tree 可能已清理）"""
        _logger.info("close: pid=%d", self._child_pid)
        self._cleanup("close")

    def get_type(self) -> str:
        """返回 PTY 后端类型标识"""
        return "win-conpty"

    def is_vt_input_enabled(self) -> bool:
        """检查子进程是否启用了 ENABLE_VIRTUAL_TERMINAL_INPUT

        当子进程启用 VT 输入模式时，它从 stdin 读取 VT 转义序列，
        而非通过 ReadConsoleInput 读取 INPUT_RECORD。

        结果缓存 3 秒以避免频繁 AttachConsole（子进程可能在运行时切换模式，
        如 TUI 应用打开菜单时启用 VT 输入）。可通过 reset_vt_input_cache() 强制刷新。

        Returns:
            True 如果子进程启用了 VT 输入模式。
        """
        import time as _time
        # 3 秒缓存：避免频繁 AttachConsole，但能在子进程切换模式后及时更新
        if hasattr(self, '_vt_input_cache') and hasattr(self, '_vt_input_cache_time'):
            if _time.monotonic() - self._vt_input_cache_time < 3.0:
                return self._vt_input_cache

        pid = self._child_pid
        if not pid:
            return False

        with _attach_conin(pid, "is_vt_input_enabled") as hConIn:
            if not hConIn:
                return False
            mode = W.DWORD()
            if _GetConsoleMode(hConIn, ctypes.byref(mode)):
                result = bool(mode.value & ENABLE_VIRTUAL_TERMINAL_INPUT)
                self._vt_input_cache = result
                self._vt_input_cache_time = _time.monotonic()
                _logger.info("is_vt_input_enabled: pid=%d mode=0x%x vt_input=%s",
                             pid, mode.value, result)
                return result

        return False

    def reset_vt_input_cache(self):
        """重置 VT 输入模式缓存，下次 is_vt_input_enabled() 会重新检查"""
        if hasattr(self, '_vt_input_cache'):
            del self._vt_input_cache

    def get_console_input_mode(self) -> Optional[int]:
        """读取子进程控制台输入模式（CONIN$ 的 ConsoleMode）

        通过 AttachConsole 附加到子进程控制台并读取当前输入模式。
        返回整数模式位，失败时返回 None。

        注意：不缓存结果，调用方如需避免频繁 AttachConsole 请自行控制频率。
        """
        pid = self._child_pid
        if not pid:
            return None

        with _attach_conin(pid, "get_console_input_mode") as hConIn:
            if not hConIn:
                return None
            mode = W.DWORD()
            if _GetConsoleMode(hConIn, ctypes.byref(mode)):
                _logger.debug("get_console_input_mode: pid=%d mode=0x%x", pid, mode.value)
                return mode.value

        return None

    def is_tui_mouse_input_enabled(self) -> bool:
        """更宽松的 TUI 鼠标模式检测

        除了 is_mouse_input_enabled 的规则外，还认为：
        - 控制台启用了 ENABLE_VIRTUAL_TERMINAL_INPUT（说明子进程按 VT 序列读输入）
        - 且处于原始输入模式
        这类程序（如 MiMo/OpenTUI）很可能是全屏 TUI，滚轮应该作为 VT 鼠标事件发送。

        注意：这里不检查 ENABLE_MOUSE_INPUT，因为某些 Windows TUI 只启用 VT_INPUT
        而忘记/不需要设置 ENABLE_MOUSE_INPUT。
        """
        pid = self._child_pid
        mode = self.get_console_input_mode()
        if mode is None:
            _logger.info("is_tui_mouse_input_enabled: pid=%d mode=None → False", pid)
            return False
        has_mouse = bool(mode & ENABLE_MOUSE_INPUT)
        has_vt = bool(mode & ENABLE_VIRTUAL_TERMINAL_INPUT)
        line = bool(mode & ENABLE_LINE_INPUT)
        echo = bool(mode & ENABLE_ECHO_INPUT)
        raw = not line and not echo
        result = raw and (has_mouse or has_vt)
        _logger.info("is_tui_mouse_input_enabled: pid=%d mode=0x%x has_mouse=%s has_vt=%s line=%s echo=%s raw=%s → %s",
                     pid, mode, has_mouse, has_vt, line, echo, raw, result)
        return result

    @staticmethod
    def _vt_sequence_to_key_records(data: bytes):
        """将 VT 键盘序列解析为 (wVirtualKeyCode, wVirtualScanCode, UnicodeChar, dwControlKeyState) 列表

        基于 WT InputStateMachineEngine.cpp 的映射表逆映射：
        CSI 光标键/功能键 → s_csiMap
        CSI Generic 键 → s_genericMap
        SS3 键 → s_ss3Map

        返回 None 表示无法识别（应保留原序列透传）。
        """
        import struct as _struct

        VK_UP = 0x26
        VK_DOWN = 0x28
        VK_LEFT = 0x25
        VK_RIGHT = 0x27
        VK_HOME = 0x24
        VK_END = 0x23
        VK_INSERT = 0x2D
        VK_DELETE = 0x2E
        VK_PRIOR = 0x21
        VK_NEXT = 0x22
        VK_F1 = 0x70
        VK_F2 = 0x71
        VK_F3 = 0x72
        VK_F4 = 0x73
        VK_F5 = 0x74
        VK_F6 = 0x75
        VK_F7 = 0x76
        VK_F8 = 0x77
        VK_F9 = 0x78
        VK_F10 = 0x79
        VK_F11 = 0x7A
        VK_F12 = 0x7B
        VK_TAB = 0x09
        ENHANCED_KEY = 0x0100

        _CSI_CURSOR_MAP = {
            ord('A'): (VK_UP, ENHANCED_KEY),
            ord('B'): (VK_DOWN, ENHANCED_KEY),
            ord('C'): (VK_RIGHT, ENHANCED_KEY),
            ord('D'): (VK_LEFT, ENHANCED_KEY),
            ord('H'): (VK_HOME, ENHANCED_KEY),
            ord('F'): (VK_END, ENHANCED_KEY),
            ord('P'): (VK_F1, 0),
            ord('Q'): (VK_F2, 0),
            ord('R'): (VK_F3, 0),
            ord('S'): (VK_F4, 0),
        }

        _CSI_GENERIC_MAP = {
            1: (VK_HOME, ENHANCED_KEY),
            2: (VK_INSERT, ENHANCED_KEY),
            3: (VK_DELETE, ENHANCED_KEY),
            4: (VK_END, ENHANCED_KEY),
            5: (VK_PRIOR, ENHANCED_KEY),
            6: (VK_NEXT, ENHANCED_KEY),
            15: (VK_F5, 0),
            17: (VK_F6, 0),
            18: (VK_F7, 0),
            19: (VK_F8, 0),
            20: (VK_F9, 0),
            21: (VK_F10, 0),
            23: (VK_F11, 0),
            24: (VK_F12, 0),
        }

        _SS3_MAP = {
            ord('A'): (VK_UP, ENHANCED_KEY),
            ord('B'): (VK_DOWN, ENHANCED_KEY),
            ord('C'): (VK_RIGHT, ENHANCED_KEY),
            ord('D'): (VK_LEFT, ENHANCED_KEY),
            ord('F'): (VK_END, ENHANCED_KEY),
            ord('H'): (VK_HOME, ENHANCED_KEY),
            ord('P'): (VK_F1, 0),
            ord('Q'): (VK_F2, 0),
            ord('R'): (VK_F3, 0),
            ord('S'): (VK_F4, 0),
        }

        results = []
        i = 0
        n = len(data)
        while i < n:
            if data[i] != 0x1b:
                i += 1
                continue
            if i + 1 >= n:
                break

            next_byte = data[i + 1]

            if next_byte == ord('['):
                # CSI 序列：ESC [ (params) final_char
                j = i + 2
                params_start = j
                while j < n and (0x30 <= data[j] <= 0x3F):
                    j += 1
                while j < n and (0x20 <= data[j] <= 0x2F):
                    j += 1
                if j >= n:
                    break
                final = data[j]
                param_str = data[params_start:j].decode('ascii', errors='replace')

                if final == ord('Z'):
                    # Shift+Tab
                    sc = _MapVirtualKeyW(VK_TAB, 0) if _MapVirtualKeyW else 0
                    results.append((i, j + 1, VK_TAB, sc, '\t', SHIFT_PRESSED))
                    i = j + 1
                    continue

                if final in _CSI_CURSOR_MAP:
                    vk, enhanced = _CSI_CURSOR_MAP[final]
                    modifier_state = 0
                    if param_str and param_str != '':
                        parts = param_str.split(';')
                        if len(parts) >= 2:
                            try:
                                mod_param = int(parts[1])
                                if mod_param > 1:
                                    vt_mod = mod_param - 1
                                    if vt_mod & 1:
                                        modifier_state |= SHIFT_PRESSED
                                    if vt_mod & 2:
                                        modifier_state |= LEFT_ALT_PRESSED
                                    if vt_mod & 4:
                                        modifier_state |= LEFT_CTRL_PRESSED
                            except ValueError:
                                pass
                    modifier_state |= enhanced
                    wch = chr(_MapVirtualKeyW(vk, 2)) if _MapVirtualKeyW else '\0'
                    sc = _MapVirtualKeyW(vk, 0) if _MapVirtualKeyW else 0
                    results.append((i, j + 1, vk, sc, wch, modifier_state))
                    i = j + 1
                    continue

                if final == ord('~') and param_str:
                    parts = param_str.split(';')
                    try:
                        key_num = int(parts[0])
                    except ValueError:
                        i = j + 1
                        continue
                    if key_num in _CSI_GENERIC_MAP:
                        vk, enhanced = _CSI_GENERIC_MAP[key_num]
                        modifier_state = 0
                        if len(parts) >= 2:
                            try:
                                mod_param = int(parts[1])
                                if mod_param > 1:
                                    vt_mod = mod_param - 1
                                    if vt_mod & 1:
                                        modifier_state |= SHIFT_PRESSED
                                    if vt_mod & 2:
                                        modifier_state |= LEFT_ALT_PRESSED
                                    if vt_mod & 4:
                                        modifier_state |= LEFT_CTRL_PRESSED
                            except ValueError:
                                pass
                        modifier_state |= enhanced
                        wch = chr(_MapVirtualKeyW(vk, 2)) if _MapVirtualKeyW else '\0'
                        sc = _MapVirtualKeyW(vk, 0) if _MapVirtualKeyW else 0
                        results.append((i, j + 1, vk, sc, wch, modifier_state))
                        i = j + 1
                        continue

                i = j + 1
                continue

            if next_byte == ord('O'):
                # SS3 序列：ESC O final_char
                if i + 2 >= n:
                    break
                final = data[i + 2]
                if final in _SS3_MAP:
                    vk, enhanced = _SS3_MAP[final]
                    modifier_state = enhanced
                    wch = chr(_MapVirtualKeyW(vk, 2)) if _MapVirtualKeyW else '\0'
                    sc = _MapVirtualKeyW(vk, 0) if _MapVirtualKeyW else 0
                    results.append((i, i + 3, vk, sc, wch, modifier_state))
                    i = i + 3
                    continue
                i = i + 2
                continue

            i += 1

        return results if results else None

    def inject_key_events(self, key_specs: list) -> bool:
        """批量注入键盘事件到子进程控制台输入缓冲区

        将 VT 键盘序列解析结果转换为 KEY_EVENT_RECORD 并写入 CONIN$。
        每个按键生成 KeyDown + KeyUp 两条记录。

        Args:
            key_specs: [(vk, scan_code, wch, modifier_state), ...] 列表

        Returns:
            True 表示所有事件写入成功。
        """
        pid = self._child_pid
        if not pid:
            _logger.warning("inject_key_events: no child pid")
            return False
        if not key_specs:
            return True

        with _attach_conin(pid, "inject_key_events") as hConIn:
            if not hConIn:
                return False

            records = []
            for vk, sc, wch, mod_state in key_specs:
                for key_down in (True, False):
                    rec = _INPUT_RECORD()
                    rec.EventType = KEY_EVENT
                    ke = rec.Event.KeyEvent
                    ke.bKeyDown = key_down
                    ke.wRepeatCount = 1
                    ke.wVirtualKeyCode = vk
                    ke.wVirtualScanCode = sc
                    ke.UnicodeChar = wch
                    ke.dwControlKeyState = mod_state
                    records.append(rec)

            buf = (_INPUT_RECORD * len(records))(*records)
            written = W.DWORD()
            ok = _WriteConsoleInputW(hConIn, buf, len(records), ctypes.byref(written))

            if ok:
                _logger.info("inject_key_events: pid=%d keys=%d records=%d written=%d",
                             pid, len(key_specs), len(records), written.value)
                return True
            else:
                err = ctypes.get_last_error()
                _logger.warning("inject_key_events: WriteConsoleInputW failed err=%d", err)
                return False

    def inject_vt_bytes(self, data: bytes) -> bool:
        """将原始 VT 字节作为 KEY_EVENT_RECORD 注入子进程控制台输入缓冲区

        ConPTY 在子进程启用鼠标跟踪后，会拦截 ConPTY 输入管道中的 SGR 鼠标序列
        并翻译为 MOUSE_EVENT_RECORD（即使 ENABLE_VIRTUAL_TERMINAL_INPUT 已启用，
        SGR 也不会作为原始字节透传到子进程 stdin）。

        此方法提供替代路径：将 SGR 字节序列的每个字节作为 KEY_EVENT_RECORD
        写入控制台输入缓冲区。当子进程通过 ReadFile(stdin) 读取时，conhost 会
        将 KEY_EVENT_RECORD 的 UnicodeChar 翻译为原始字符字节，从而将完整的
        SGR 序列送达子进程 stdin。

        不调用 SetConsoleMode，避免干扰子进程的控制台模式设置。

        Args:
            data: 要注入的原始字节（如 SGR 鼠标序列 b'\\x1b[<0;41;13M'）。

        Returns:
            True 表示注入成功。
        """
        pid = self._child_pid
        if not pid:
            _logger.warning("inject_vt_bytes: no child pid")
            return False

        with _attach_conin(pid, "inject_vt_bytes") as hConIn:
            if not hConIn:
                return False

            records = []
            for byte_val in data:
                ch = chr(byte_val)
                for key_down in (True, False):
                    rec = _INPUT_RECORD()
                    rec.EventType = KEY_EVENT
                    ke = rec.Event.KeyEvent
                    ke.bKeyDown = key_down
                    ke.wRepeatCount = 1
                    ke.wVirtualKeyCode = byte_val if byte_val < 0x80 else 0
                    ke.wVirtualScanCode = 0
                    ke.UnicodeChar = ch
                    ke.dwControlKeyState = 0
                    records.append(rec)

            buf = (_INPUT_RECORD * len(records))(*records)
            written = W.DWORD()
            ok = _WriteConsoleInputW(hConIn, buf, len(records), ctypes.byref(written))
            if ok:
                _logger.info("inject_vt_bytes: pid=%d bytes=%d records=%d written=%d data=%r",
                             pid, len(data), len(records), written.value, data[:200])
                return True
            else:
                err = ctypes.get_last_error()
                _logger.warning("inject_vt_bytes: WriteConsoleInputW failed err=%d", err)
                return False

    @staticmethod
    def _decode_sgr_to_mouse_record(x: int, y: int, button: int, is_release: bool,
                                     double_click: bool = False):
        """SGR button 解码 → (dwButtonState, dwEventFlags, dwControlKeyState)

        基于 Windows Terminal _windowsButtonToSGREncoding 逆映射：
          SGR button 编码 → Windows dwButtonState + dwEventFlags

        SGR button 位编码：
          bits 0-1: 0=left, 1=middle, 2=right, 3=release/hover
          bit 5 (0x20): drag/motion
          bit 6 (0x40): wheel (0x40=up, 0x41=down, 0x42=left, 0x43=right)
          bit 2 (0x04): shift
          bit 3 (0x08): alt
          bit 4 (0x10): ctrl

        Args:
            double_click: 为 True 时在 press 事件的 dwEventFlags 中设置 DOUBLE_CLICK。
                         Windows TUI 程序（tcell/tview/gdu）依赖此标志识别双击。
        """
        sgr_modifiers = button & 0x1c  # shift|alt|ctrl
        win_modifiers = 0
        if sgr_modifiers & 0x04:
            win_modifiers |= SHIFT_PRESSED
        if sgr_modifiers & 0x08:
            win_modifiers |= LEFT_ALT_PRESSED
        if sgr_modifiers & 0x10:
            win_modifiers |= LEFT_CTRL_PRESSED

        button_state = 0
        event_flags = 0

        is_wheel = (button & 0x40) != 0
        is_drag = (button & 0x20) != 0
        base_button = button & 0x03

        if is_wheel:
            wheel_code = button & 0x03  # 0=up, 1=down, 2=left, 3=right
            if wheel_code == 0:
                delta = WHEEL_DELTA  # up
            elif wheel_code == 1:
                delta = -WHEEL_DELTA  # down
            elif wheel_code == 2:
                delta = -WHEEL_DELTA  # left (use HWHEELED)
                event_flags = MOUSE_HWHEELED
            else:
                delta = WHEEL_DELTA  # right
                event_flags = MOUSE_HWHEELED
            if event_flags != MOUSE_HWHEELED:
                event_flags = MOUSE_WHEELED
            button_state = (delta & 0xFFFF) << 16
        elif is_drag:
            event_flags = MOUSE_MOVED
            if base_button == 0:
                button_state = FROM_LEFT_1ST_BUTTON_PRESSED
            elif base_button == 1:
                button_state = FROM_LEFT_2ND_BUTTON_PRESSED
            elif base_button == 2:
                button_state = RIGHTMOST_BUTTON_PRESSED
            else:
                button_state = 0
                event_flags = 0  # no button pressed, just a hover
        elif is_release or base_button == 3:
            button_state = 0
        else:
            if base_button == 0:
                button_state = FROM_LEFT_1ST_BUTTON_PRESSED
            elif base_button == 1:
                button_state = FROM_LEFT_2ND_BUTTON_PRESSED
            elif base_button == 2:
                button_state = RIGHTMOST_BUTTON_PRESSED
            if double_click and not is_release:
                event_flags = DOUBLE_CLICK

        return button_state, event_flags, win_modifiers

    def _build_mouse_record(self, x: int, y: int, button: int, is_release: bool,
                            double_click: bool = False) -> _INPUT_RECORD:
        """构造单个 MOUSE_EVENT_RECORD INPUT_RECORD

        Args:
            double_click: 为 True 时在 press 事件的 dwEventFlags 中设置 DOUBLE_CLICK。
        """
        button_state, event_flags, win_modifiers = self._decode_sgr_to_mouse_record(
            x, y, button, is_release, double_click=double_click)
        rec = _INPUT_RECORD()
        rec.EventType = MOUSE_EVENT
        me = ctypes.cast(ctypes.byref(rec.Event), ctypes.POINTER(_MOUSE_EVENT_RECORD)).contents
        me.dwMousePosition.X = x
        me.dwMousePosition.Y = y
        me.dwButtonState = button_state
        me.dwControlKeyState = win_modifiers
        me.dwEventFlags = event_flags
        return rec

    def inject_mouse_events(self, events: List[tuple]) -> bool:
        """批量注入多个鼠标事件到子进程控制台输入缓冲区

        将多个事件在一次 AttachConsole 周期内全部写入，避免多次 AttachConsole/FreeConsole
        的开销，并确保 VT_INPUT 在整个事件序列期间保持禁用，让 tcell 的 scanInput
        goroutine 能通过 ReadConsoleInput 完整读取整个事件序列。

        这对双击等需要多个事件协同的鼠标动作至关重要：tview 的 fireMouseActions
        需要按顺序看到 (press → release → press[DOUBLE_CLICK] → release) 才能识别为双击。
        如果每个事件单独注入并立即恢复 VT_INPUT，tcell 可能在 VT_INPUT 恢复后才读到
        部分事件，导致事件丢失或乱序。

        Args:
            events: [(x, y, button, is_release), ...] 或
                    [(x, y, button, is_release, double_click), ...] 元组列表

        Returns:
            True 表示所有事件写入成功。
        """
        pid = self._child_pid
        if not pid:
            _logger.warning("inject_mouse_events: no child pid")
            return False
        if not events:
            return True

        with _attach_conin(pid, "inject_mouse_events") as hConIn:
            if not hConIn:
                return False

            mode = W.DWORD()
            if _GetConsoleMode(hConIn, ctypes.byref(mode)):
                new_mode = mode.value | ENABLE_MOUSE_INPUT | ENABLE_EXTENDED_FLAGS
                new_mode &= ~ENABLE_QUICK_EDIT_MODE
                if _SetConsoleMode(hConIn, new_mode):
                    verify_mode = W.DWORD()
                    if _GetConsoleMode(hConIn, ctypes.byref(verify_mode)):
                        _logger.info("inject_mouse_events: CONIN$ mode 0x%x -> 0x%x (verify=0x%x, events=%d)",
                                     mode.value, new_mode, verify_mode.value, len(events))
                    else:
                        _logger.info("inject_mouse_events: CONIN$ mode 0x%x -> 0x%x (verify failed, events=%d)",
                                     mode.value, new_mode, len(events))
                else:
                    err = ctypes.get_last_error()
                    _logger.warning("inject_mouse_events: SetConsoleMode failed err=%d", err)
            else:
                err = ctypes.get_last_error()
                _logger.warning("inject_mouse_events: GetConsoleMode failed err=%d", err)

            records = []
            for ev in events:
                x, y, button, is_release = ev[0], ev[1], ev[2], ev[3]
                double_click = ev[4] if len(ev) > 4 else False
                rec = self._build_mouse_record(x, y, button, is_release,
                                               double_click=double_click)
                records.append(rec)

            buf = (_INPUT_RECORD * len(records))(*records)
            written = W.DWORD()
            ok = _WriteConsoleInputW(hConIn, buf, len(records), ctypes.byref(written))

            if ok:
                for ev, rec in zip(events, records):
                    x, y, button, is_release = ev[0], ev[1], ev[2], ev[3]
                    double_click = ev[4] if len(ev) > 4 else False
                    me = ctypes.cast(ctypes.byref(rec.Event), ctypes.POINTER(_MOUSE_EVENT_RECORD)).contents
                    _logger.debug("inject_mouse_events: pid=%d pos=(%d,%d) button=%d release=%s "
                                  "dc=%s state=0x%x flags=0x%x mods=0x%x",
                                  pid, x, y, button, is_release, double_click,
                                  me.dwButtonState, me.dwEventFlags, me.dwControlKeyState)
                import time as _time
                _time.sleep(0.05)  # 50ms
                _logger.info("inject_mouse_events: %d events written, waited 50ms for tcell", len(records))
                return True
            else:
                err = ctypes.get_last_error()
                _logger.warning("inject_mouse_events: WriteConsoleInputW failed err=%d", err)
                return False

    def inject_mouse_event(self, x: int, y: int, button: int, is_release: bool,
                           control_key_state: int = 0) -> bool:
        """直接注入单个鼠标事件到子进程控制台输入缓冲区

        保留向后兼容的单事件接口。内部调用 inject_mouse_events 批量接口。

        Args:
            x:                  列坐标（0-based，Windows 控制台坐标）。
            y:                  行坐标（0-based）。
            button:             SGR button 编码（含 modifier/drag/wheel 位）。
            is_release:         True 表示按钮释放（SGR 'm' 后缀）。
            control_key_state:  额外的 dwControlKeyState（已弃用，从 button 解析）。
        """
        return self.inject_mouse_events([(x, y, button, is_release)])

    def get_child_pid(self):
        return self._child_pid

    def get_exit_code(self) -> Optional[int]:
        """获取子进程退出码

        通过 GetExitCodeProcess 获取子进程的退出码。

        Returns:
            退出码（int），若进程仍在运行则返回 None。
        """
        if not self._ph:
            return None
        try:
            code = W.DWORD(0)
            if not _GetExitCodeProcess(self._ph, ctypes.byref(code)):
                return None
            if code.value == STILL_ACTIVE:
                return None
            return code.value
        except Exception:
            return None

    # ---- 进程树追踪已迁出到 process/tracker（register_root 时注入）----
