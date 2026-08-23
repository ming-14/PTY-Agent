"""attend 交互引擎 — CLI 接管会话为完整实时终端

客户端把 daemon 透传的原始输出字节流直接写入本机终端（用户终端原生渲染，
与直接运行一致）；用 ReadConsoleInputW（Windows）/ termios raw（Unix）
读取原始输入事件并映射为帧发给 daemon。

- 输出：零解析透传（attend_output 原始字节 → stdout）
- 输入：可打印字符/IME 走 attend_input（write_input），
        特殊键/修饰组合走 attend_key（daemon 模式感知编码）
- 鼠标：MOUSE_EVENT_RECORD → attend_mouse；随 attend_mouse_mode 动态切换 quick-edit
- 尺寸：轮询 GetConsoleScreenBufferInfo（50ms，参照 terminal-injector WtSizeWatcher）
- 分离：Ctrl+\\ → attend_detach；Ctrl+C 透传会话
"""

import ctypes
import os
import re
import shutil
import sys
import threading
import time
from ctypes import wintypes
from typing import Optional

from ..config.common import IS_WINDOWS
from ..protocol.envelope import request as _env_request, unwrap as _env_unwrap
from ..protocol.message import Message
from ..logging import get_logger

_logger = get_logger("pty-client")

# wezterm 修饰键位（与 web 前端 KeyModifiers 一致）
MOD_SHIFT = 1 << 1
MOD_ALT = 1 << 2
MOD_CTRL = 1 << 3

# 分离键：Ctrl+\（FS 控制符 0x1C，或字面反斜杠 + Ctrl）
_DETACH_CHARS = (0x1C, ord("\\"))

# 尺寸轮询间隔（毫秒）
_SIZE_POLL_MS = 50

# VK → wezterm 特殊键名（与 web 前端 _SPECIAL_KEY_MAP 一致）
_VK_SPECIAL = {
    0x0D: "Enter",       # VK_RETURN
    0x09: "Tab",         # VK_TAB
    0x08: "Backspace",   # VK_BACK
    0x1B: "Esc",         # VK_ESCAPE
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x24: "Home", 0x23: "End",
    0x21: "PageUp", 0x22: "PageDown",
    0x2D: "Insert", 0x2E: "Delete",
    0x20: "Space",       # VK_SPACE
}

# 输入记录事件类型
_KEY_EVENT = 0x0001
_MOUSE_EVENT = 0x0002

# MOUSE_EVENT_RECORD 按钮位 / 标志
_LBUTTON = 0x0001
_RBUTTON = 0x0002
_MBUTTON = 0x0004
_MOUSE_WHEELED = 0x0004
_MOUSE_HWHEELED = 0x0008
_MOUSE_MOVED = 0x0001


def _control_mods(control_state: int) -> int:
    """dwControlKeyState → wezterm 修饰键位掩码"""
    mods = 0
    if control_state & 0x0010:  # SHIFT_PRESSED
        mods |= MOD_SHIFT
    if control_state & (0x0001 | 0x0002):  # RIGHT/LEFT_ALT_PRESSED
        mods |= MOD_ALT
    if control_state & (0x0004 | 0x0008):  # RIGHT/LEFT_CTRL_PRESSED
        mods |= MOD_CTRL
    return mods


def _is_detach(vk: int, ch, mods: int) -> bool:
    """分离键判定：Ctrl+\（FS 控制符 0x1C 或字面反斜杠 + Ctrl）"""
    if not (mods & MOD_CTRL):
        return False
    if isinstance(ch, str):
        ch = ord(ch) if len(ch) == 1 else 0
    if ch in _DETACH_CHARS:
        return True
    # 部分布局下 Ctrl+\ 的 VK 为 VK_OEM_5(0xDC)
    return vk == 0xDC


def map_key_event(
    vk: int, ch, control_state: int, surrogate: dict
) -> list:
    """KEY_EVENT_RECORD 字段 → 待发送帧列表（可打印文本 / 特殊键）

    surrogate 为高代理缓存 dict（跨调用），中文/emoji 等由 IME 组合输入时
    可能以代理对到达，需配对后转文本帧。

    Returns:
        list[dict]：0 或多个帧（attend_input / attend_key）。
    """
    # 控制台 uChar 为 WCHAR（ctypes 读出 str），归一为 codepoint int
    if isinstance(ch, str):
        ch = ord(ch) if len(ch) == 1 else 0
    frames = []
    mods = _control_mods(control_state)

    # 单独修饰键按下（无字符）忽略
    if ch == 0 and vk in (0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5):
        return frames

    # 特殊键（方向/F/编辑/Enter/Tab/Backspace/Esc/Space）
    if vk in _VK_SPECIAL:
        frames.append({"type": "attend_key", "key": _VK_SPECIAL[vk], "mods": mods})
        return frames
    if 0x70 <= vk <= 0x87:  # VK_F1..VK_F24
        frames.append({"type": "attend_key", "key": f"F{vk - 0x70 + 1}", "mods": mods})
        return frames

    # 非 BMP 代理对
    if 0xD800 <= ch <= 0xDBFF:  # 高代理缓存
        surrogate["high"] = (ch, mods)
        return frames
    if 0xDC00 <= ch <= 0xDFFF:  # 低代理：与缓存高代理组合
        high = surrogate.pop("high", None)
        if high:
            cp = 0x10000 + ((high[0] - 0xD800) << 10) + (ch - 0xDC00)
            frames.append({"type": "attend_input", "data": chr(cp)})
        return frames
    if "high" in surrogate:  # 孤立高代理残留丢弃
        surrogate.pop("high")

    # Ctrl+字母：控制字符还原为 letter + CTRL（对齐 web 前端）
    if 1 <= ch <= 26 and (mods & MOD_CTRL):
        frames.append(
            {
                "type": "attend_key",
                "key": chr(ch + 96),
                "mods": (mods | MOD_CTRL) & ~MOD_SHIFT,
            }
        )
        return frames

    # 可打印字符
    if ch >= 0x20:
        # 大写 + Shift → 小写 + 保留 SHIFT（让程序感知 shift 状态）
        if (mods & MOD_SHIFT) and 0x41 <= ch <= 0x5A:
            frames.append(
                {"type": "attend_key", "key": chr(ch + 32), "mods": mods}
            )
        elif (mods & MOD_CTRL) or (mods & MOD_ALT):
            # 带修饰的字符需模式感知编码（Alt+a 等）
            frames.append({"type": "attend_key", "key": chr(ch), "mods": mods})
        else:
            # 普通可打印（含中文/emoji BMP）→ 文本帧，经 write_input 直达
            frames.append({"type": "attend_input", "data": chr(ch)})
    return frames


def map_mouse_event(
    x: int,
    y: int,
    button_state: int,
    control_state: int,
    event_flags: int,
    prev_button_state: int,
) -> list:
    """MOUSE_EVENT_RECORD 字段 → attend_mouse 帧列表（0-based 单元格坐标）

    按下/释放靠跨事件按钮状态差分（参照 InputRecordToVt）；拖拽/悬停移动在
    鼠标追踪开启时才有意义（未开启时 quick-edit 打开、事件不进输入缓冲）。
    """
    frames = []
    mods = _control_mods(control_state)

    if event_flags & _MOUSE_WHEELED:  # 纵向滚轮（高字为增量，正=上滚）
        wheel = (button_state >> 16) & 0xFFFF
        if wheel >= 0x8000:
            wheel -= 0x10000
        button = "wheel_up" if wheel > 0 else "wheel_down"
        frames.append(
            {"type": "attend_mouse", "x": x, "y": y, "kind": "press", "button": button, "mods": mods}
        )
        return frames
    if event_flags & _MOUSE_HWHEELED:
        # 横向滚轮：wezterm 编码器无 hwheel 按钮，忽略
        return frames

    for mask, name in ((_LBUTTON, "left"), (_MBUTTON, "middle"), (_RBUTTON, "right")):
        was_down = bool(prev_button_state & mask)
        is_down = bool(button_state & mask)
        if not was_down and is_down:
            frames.append(
                {"type": "attend_mouse", "x": x, "y": y, "kind": "press", "button": name, "mods": mods}
            )
        elif was_down and not is_down:
            frames.append(
                {"type": "attend_mouse", "x": x, "y": y, "kind": "release", "button": name, "mods": mods}
            )

    if event_flags & _MOUSE_MOVED:
        held = next((n for m, n in ((_LBUTTON, "left"), (_MBUTTON, "middle"), (_RBUTTON, "right")) if button_state & m), "none")
        frames.append(
            {"type": "attend_mouse", "x": x, "y": y, "kind": "move", "button": held, "mods": mods}
        )
    return frames


def decode_output_payload(data) -> bytes:
    """attend_output 载荷（latin-1 无损映射）还原为原始字节"""
    if isinstance(data, str):
        return data.encode("latin-1")
    return bytes(data)


# 窗口操作序列（CSI ... t，xterm window ops）。应用发起的窗口查询（如
# \x1b[14t）由 daemon 终端模型应答（如 \x1b[4;600;1200t），应答回写到 PTY
# 输入后在应用 ECHO 模式下被回显进输出流；原始透传会原样上屏。attend 自身
# 管理尺寸，应用窗口操作不应到达用户终端（且会误调整用户窗口），故剥离。
_WINDOW_OP_RE_B = re.compile(rb"\x1b\[[0-9;?]*t")
_WINDOW_OP_RE = re.compile(r"\x1b\[[0-9;?]*t")


def _strip_window_ops(data: bytes) -> bytes:
    """剥离输出字节流中的窗口操作序列（CSI ... t）"""
    return _WINDOW_OP_RE_B.sub(b"", data)


def _strip_window_ops_text(text: str) -> str:
    """剥离文本中的窗口操作序列（replay/resync 用）"""
    return _WINDOW_OP_RE.sub("", text)


# ════════════════════════════════════════════════════════════
# Windows 控制台 API（惰性加载 ctypes 绑定）
# ════════════════════════════════════════════════════════════

_win = None


def _winapi():
    global _win
    if _win is not None:
        return _win

    class COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [
            ("Left", wintypes.SHORT),
            ("Top", wintypes.SHORT),
            ("Right", wintypes.SHORT),
            ("Bottom", wintypes.SHORT),
        ]

    class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", COORD),
            ("dwCursorPosition", COORD),
            ("wAttributes", wintypes.WORD),
            ("srWindow", SMALL_RECT),
            ("dwMaximumWindowSize", COORD),
        ]

    class KEY_EVENT_RECORD(ctypes.Structure):
        _fields_ = [
            ("bKeyDown", wintypes.BOOL),
            ("wRepeatCount", wintypes.WORD),
            ("wVirtualKeyCode", wintypes.WORD),
            ("wVirtualScanCode", wintypes.WORD),
            ("uChar", wintypes.WCHAR),
            ("dwControlKeyState", wintypes.DWORD),
        ]

    class MOUSE_EVENT_RECORD(ctypes.Structure):
        _fields_ = [
            ("dwMousePosition", COORD),
            ("dwButtonState", wintypes.DWORD),
            ("dwControlKeyState", wintypes.DWORD),
            ("dwEventFlags", wintypes.DWORD),
        ]

    class WINDOW_BUFFER_SIZE_RECORD(ctypes.Structure):
        _fields_ = [("dwSize", COORD)]

    class MENU_EVENT_RECORD(ctypes.Structure):
        _fields_ = [("dwCommandId", wintypes.UINT)]

    class FOCUS_EVENT_RECORD(ctypes.Structure):
        _fields_ = [("bSetFocus", wintypes.BOOL)]

    class _INPUT_RECORD_UNION(ctypes.Union):
        _fields_ = [
            ("KeyEvent", KEY_EVENT_RECORD),
            ("MouseEvent", MOUSE_EVENT_RECORD),
            ("WindowBufferSizeEvent", WINDOW_BUFFER_SIZE_RECORD),
            ("MenuEvent", MENU_EVENT_RECORD),
            ("FocusEvent", FOCUS_EVENT_RECORD),
        ]

    class INPUT_RECORD(ctypes.Structure):
        _anonymous_ = ("Event",)
        _fields_ = [("EventType", wintypes.WORD), ("Event", _INPUT_RECORD_UNION)]

    k32 = ctypes.windll.kernel32
    _win = {
        "k32": k32,
        "INPUT_RECORD": INPUT_RECORD,
        "COORD": COORD,
        "CONSOLE_SCREEN_BUFFER_INFO": CONSOLE_SCREEN_BUFFER_INFO,
    }
    return _win


def _win_size() -> tuple:
    """当前控制台窗口尺寸 (cols, rows)：srWindow 视口宽高"""
    w = _winapi()
    h_out = w["k32"].GetStdHandle(-11)
    info = w["CONSOLE_SCREEN_BUFFER_INFO"]()
    if not w["k32"].GetConsoleScreenBufferInfo(h_out, ctypes.byref(info)):
        return (80, 24)
    cols = info.srWindow.Right - info.srWindow.Left + 1
    rows = info.srWindow.Bottom - info.srWindow.Top + 1
    return (cols, rows)


# ════════════════════════════════════════════════════════════
# 控制台状态管理（Windows：raw 输入 + VT 输出 + quick-edit 切换）
# ════════════════════════════════════════════════════════════

# 输入模式位
_ENABLE_PROCESSED_INPUT = 0x0001
_ENABLE_LINE_INPUT = 0x0002
_ENABLE_ECHO_INPUT = 0x0004
_ENABLE_WINDOW_INPUT = 0x0008
_ENABLE_MOUSE_INPUT = 0x0010
_ENABLE_QUICK_EDIT_MODE = 0x0040
_ENABLE_EXTENDED_FLAGS = 0x0080
# 输出模式位
_ENABLE_PROCESSED_OUTPUT = 0x0001
_ENABLE_WRAP_AT_EOL_OUTPUT = 0x0002
_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004


class _ConsoleState:
    """保存/恢复控制台模式与缓冲尺寸；进入 raw 输入 + VT 输出接管"""

    def __init__(self):
        self._active = False
        self._stop_event = threading.Event()
        self._in_handle = None
        self._out_handle = None
        self._in_mode = None
        self._out_mode = None
        self._buf_size = None
        self._orig_attrs = None  # Unix termios

    def begin(self, mouse_tracking: bool):
        if IS_WINDOWS:
            self._begin_win(mouse_tracking)
        else:
            self._begin_unix()
        self._active = True

    def restore(self):
        if not self._active:
            return
        self._active = False
        self._stop_event.set()
        if IS_WINDOWS:
            self._restore_win()
        else:
            self._restore_unix()

    def set_quick_edit(self, enabled: bool):
        """鼠标追踪状态变化时切换 quick-edit（开=可拖选，关=鼠标交应用）"""
        if not self._active or not IS_WINDOWS:
            return
        self._apply_input_mode(quick_edit=enabled)

    # ── Windows ──────────────────────────────────────────────

    def _begin_win(self, mouse_tracking: bool):
        w = _winapi()
        k32 = w["k32"]
        h_in = k32.GetStdHandle(-10)
        h_out = k32.GetStdHandle(-11)
        in_mode = wintypes.DWORD(0)
        out_mode = wintypes.DWORD(0)
        k32.GetConsoleMode(h_in, ctypes.byref(in_mode))
        k32.GetConsoleMode(h_out, ctypes.byref(out_mode))
        self._in_handle, self._out_handle = h_in, h_out
        self._in_mode, self._out_mode = int(in_mode.value), int(out_mode.value)

        # 保存并缩小缓冲尺寸（避免控制台自身滚动干扰接管画面）
        info = w["CONSOLE_SCREEN_BUFFER_INFO"]()
        if k32.GetConsoleScreenBufferInfo(h_out, ctypes.byref(info)):
            self._buf_size = (int(info.dwSize.X), int(info.dwSize.Y))
            try:
                size = w["COORD"](int(info.srWindow.Right - info.srWindow.Left + 1),
                                  int(info.srWindow.Bottom - info.srWindow.Top + 1))
                k32.SetConsoleScreenBufferSize(h_out, size)
            except Exception:
                pass

        # 输出：启用 VT 处理
        k32.SetConsoleMode(
            h_out,
            self._out_mode
            | _ENABLE_VIRTUAL_TERMINAL_PROCESSING
            | _ENABLE_PROCESSED_OUTPUT
            | _ENABLE_WRAP_AT_EOL_OUTPUT,
        )
        # 输入：raw（去 processed/line/echo）+ 开 mouse/window 事件；quick-edit 按追踪状态
        self._apply_input_mode(quick_edit=not mouse_tracking)
        # 清屏接管
        _write_bytes(b"\x1b[2J\x1b[H\x1b[?25l")
        sys.stdout.buffer.flush()

    def _restore_win(self):
        w = _winapi()
        k32 = w["k32"]
        _write_bytes(b"\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.buffer.flush()
        if self._out_mode is not None and self._out_handle is not None:
            k32.SetConsoleMode(self._out_handle, self._out_mode)
        if self._in_mode is not None and self._in_handle is not None:
            k32.SetConsoleMode(self._in_handle, self._in_mode)
            # 冲刷遗留输入事件，避免泄露给后续 shell
            try:
                k32.FlushConsoleInputBuffer(self._in_handle)
            except Exception:
                pass
        if self._buf_size is not None and self._out_handle is not None:
            try:
                k32.SetConsoleScreenBufferSize(
                    self._out_handle, w["COORD"](self._buf_size[0], self._buf_size[1])
                )
            except Exception:
                pass

    def _apply_input_mode(self, quick_edit: bool):
        if self._in_handle is None:
            return
        w = _winapi()
        mode = _ENABLE_WINDOW_INPUT | _ENABLE_MOUSE_INPUT | _ENABLE_EXTENDED_FLAGS
        if quick_edit:
            mode |= _ENABLE_QUICK_EDIT_MODE
        w["k32"].SetConsoleMode(self._in_handle, mode)

    # ── Unix ─────────────────────────────────────────────────

    def _begin_unix(self):
        import termios
        import tty

        self._orig_attrs = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
        _write_bytes(b"\x1b[2J\x1b[H\x1b[?25l")
        sys.stdout.buffer.flush()

    def _restore_unix(self):
        _write_bytes(b"\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.buffer.flush()
        if self._orig_attrs is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._orig_attrs)


def _write_bytes(raw: bytes):
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def _write_text(text: str):
    sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def _write_clipboard(text: str):
    """写入系统剪贴板（OSC 52 应用剪贴板写；Win32 CF_UNICODETEXT）

    非 Windows / 剪贴板不可用时静默忽略（OSC 52 是尽力而为，不打扰用户）。
    """
    if not text or not IS_WINDOWS:
        return
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    data = text.encode("utf-16-le") + b"\x00\x00"
    size = len(data)
    if not user32.OpenClipboard(None):
        return
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if h:
            p = kernel32.GlobalLock(h)
            if p:
                ctypes.memmove(p, data, size)
                kernel32.GlobalUnlock(h)
            user32.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        user32.CloseClipboard()


# ════════════════════════════════════════════════════════════
# attend 客户端主流程
# ════════════════════════════════════════════════════════════

def _emit_msg(text: str):
    """面向用户的消息（项目约定：stderr + (PTY-Agent message: ...) 前缀）"""
    print(f"(PTY-Agent message: {text})", file=sys.stderr)


class _AttendClient:
    """单个 attend 会话的客户端控制：握手 → 接管 → 帧循环 → 恢复"""

    def __init__(self, client, session_id: str):
        self._client = client
        self._sid = session_id
        self._sock = None
        self._out_signer = None
        self._state = _ConsoleState()
        self._ended_info = None
        self._error_msg = None
        self._mouse_tracking = False

    def run(self) -> int:
        self._sock = self._client._connect(autostart=True)
        self._sock.settimeout(None)
        self._out_signer = Message.get_outbound_signer()
        try:
            cols, rows = self._current_size()
            self._send({"type": "attend", "id": self._sid, "cols": cols, "rows": rows})
            if not self._await_ready():
                return 1
            self._state.begin(self._mouse_tracking)
            self._start_input_thread()
            self._start_size_thread()
            self._frame_loop()
            self._state.restore()
            if self._error_msg:
                _emit_msg(self._error_msg)
                return 1
            if self._ended_info is not None:
                ec = self._ended_info.get("exitCode")
                suffix = f" (exit code: {ec})" if ec is not None else ""
                _emit_msg(f"Session '{self._sid}' ended.{suffix}")
            else:
                _emit_msg(f"Detached from session '{self._sid}'.")
            return 0
        except ConnectionError as e:
            _emit_msg(f"connection lost: {e}")
            return 1
        finally:
            self._state.restore()
            try:
                self._sock.close()
            except OSError:
                pass

    # ── 帧收发 ───────────────────────────────────────────────

    def _send(self, frame: dict):
        msg = _env_request(frame.get("type", ""), frame)
        if self._client._credential_provider is not None:
            self._client._credential_provider.enrich(msg)
        Message.send(self._sock, msg)

    def _await_ready(self) -> bool:
        while True:
            msg = Message.recv(self._sock)
            if msg is None:
                _emit_msg("daemon 连接已断开")
                return False
            try:
                _, body, _ = _env_unwrap(msg)
            except Exception:
                continue
            t = body.get("type")
            if t == "attend_ready":
                self._mouse_tracking = bool(body.get("mouseTracking", False))
                return True
            if t == "error":
                _emit_msg(body.get("message", "attend failed"))
                return False

    def _frame_loop(self):
        while not self._state._stop_event.is_set():
            msg = Message.recv(self._sock)
            if msg is None:
                break
            try:
                _, body, _ = _env_unwrap(msg)
            except Exception:
                continue
            t = body.get("type")
            if t == "attend_replay":
                self._render_replay(body)
            elif t == "attend_resync":
                # 丢帧重同步：先清屏再全屏重绘（残留行/乱码需清除）
                _write_bytes(b"\x1b[2J\x1b[H")
                self._render_replay(body)
            elif t == "attend_output":
                _write_bytes(_strip_window_ops(decode_output_payload(body.get("text", ""))))
            elif t == "attend_mouse_mode":
                self._state.set_quick_edit(bool(body.get("tracking", False)))
            elif t == "attend_clipboard":
                # OSC 52 剪贴板写：写入系统剪贴板
                try:
                    _write_clipboard(body.get("data", ""))
                except Exception as e:
                    _logger.debug("attend_clipboard 写入失败: %s", e)
            elif t == "attend_ended":
                self._ended_info = body
                break
            elif t == "error":
                self._error_msg = body.get("message", "attend error")
                break

    def _render_replay(self, body: dict):
        if body.get("subprocess"):
            _write_text(_strip_window_ops_text(body.get("text", "")))
            err = body.get("stderr", "")
            if err:
                _write_text(_strip_window_ops_text(err))
            return
        _write_text(_strip_window_ops_text(body.get("text", "")))

    # ── 输入线程 ─────────────────────────────────────────────

    def _start_input_thread(self):
        def _loop():
            # 线程局部：发送签名器（与连接线程一致）
            Message.set_outbound_signer(self._out_signer)
            self._console_input_loop()

        t = threading.Thread(
            target=_loop, name=f"attend-input-{self._sid}", daemon=True
        )
        t.start()

    def _start_size_thread(self):
        """尺寸轮询线程：50ms 检测窗口尺寸变化 → attend_resize（参照 WtSizeWatcher）"""
        def _loop():
            Message.set_outbound_signer(self._out_signer)
            self._size_loop()

        t = threading.Thread(
            target=_loop, name=f"attend-size-{self._sid}", daemon=True
        )
        t.start()

    def _size_loop(self):
        last = None
        while not self._state._stop_event.is_set():
            try:
                size = self._current_size()
            except Exception:
                size = None
            if size is not None and size != last:
                last = size
                self._send_or_stop(
                    {"type": "attend_resize", "cols": size[0], "rows": size[1]}
                )
            time.sleep(_SIZE_POLL_MS / 1000.0)

    def _console_input_loop(self):
        if IS_WINDOWS:
            self._win_input_loop()
        else:
            self._unix_input_loop()

    def _send_or_stop(self, frame) -> bool:
        if self._state._stop_event.is_set():
            return False
        try:
            self._send(frame)
            return True
        except (ConnectionError, OSError):
            return False

    def _win_input_loop(self):
        w = _winapi()
        k32 = w["k32"]
        h_in = k32.GetStdHandle(-10)
        surrogate: dict = {}
        prev_button = 0
        while not self._state._stop_event.is_set():
            n = wintypes.DWORD(0)
            if not k32.GetNumberOfConsoleInputEvents(h_in, ctypes.byref(n)) or n.value <= 0:
                time.sleep(0.01)
                continue
            count = min(int(n.value), 64)
            buf = (w["INPUT_RECORD"] * count)()
            num = wintypes.DWORD(0)
            if not k32.ReadConsoleInputW(h_in, buf, count, ctypes.byref(num)):
                time.sleep(0.01)
                continue
            for i in range(int(num.value)):
                rec = buf[i]
                et = rec.EventType
                if et == _KEY_EVENT:
                    ke = rec.KeyEvent
                    if not ke.bKeyDown:
                        continue
                    mods = _control_mods(ke.dwControlKeyState)
                    if _is_detach(ke.wVirtualKeyCode, ke.uChar, mods):
                        self._send_or_stop({"type": "attend_detach"})
                        self._state._stop_event.set()
                        return
                    for f in map_key_event(
                        ke.wVirtualKeyCode, ke.uChar, ke.dwControlKeyState, surrogate
                    ):
                        if not self._send_or_stop(f):
                            return
                elif et == _MOUSE_EVENT:
                    me = rec.MouseEvent
                    for f in map_mouse_event(
                        me.dwMousePosition.X,
                        me.dwMousePosition.Y,
                        me.dwButtonState,
                        me.dwControlKeyState,
                        me.dwEventFlags,
                        prev_button,
                    ):
                        if not self._send_or_stop(f):
                            return
                    prev_button = me.dwButtonState
            time.sleep(0.005)

    def _unix_input_loop(self):
        """Unix 回退：raw 读取 stdin 字节流原样转发（模式感知编码仅 Windows）"""
        import termios

        fd = sys.stdin.fileno()
        while not self._state._stop_event.is_set():
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                time.sleep(0.01)
                continue
            text = chunk.decode("utf-8", errors="replace")
            # Ctrl+\（0x1C）→ 分离
            if "\x1c" in text:
                self._send_or_stop({"type": "attend_detach"})
                self._state._stop_event.set()
                return
            if not self._send_or_stop({"type": "attend_input", "data": text}):
                return
            time.sleep(0.005)

    # ── 尺寸 ─────────────────────────────────────────────────

    def _current_size(self) -> tuple:
        if IS_WINDOWS:
            return _win_size()
        try:
            size = shutil.get_terminal_size()
            return (size.columns, size.lines)
        except Exception:
            return (80, 24)


def run_attend(client, session_id: str) -> int:
    """attend 入口：由 Client.cmd_attend 委托"""
    return _AttendClient(client, session_id).run()
