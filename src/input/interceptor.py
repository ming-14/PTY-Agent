"""InputInterceptor — 输入拦截策略

负责在数据写入 PTY 之前进行拦截处理：
- SGR 鼠标序列拦截与注入（Windows ConPTY）
- 键盘 VT 序列拦截与注入（Windows ConPTY）
- 鼠标动作执行（click/hover/scroll/drag/press）
- 控制台鼠标模式轮询

这些逻辑与 Session 的核心协调职责无关，且包含大量 Windows 平台特定代码。
"""

import os
import re
import time
import logging
from typing import Optional, Callable

from ..config.common import IS_WINDOWS
from ..output import PendingEvent
from .mouse import MouseActionEncoder, MouseError, Coord, grep_screen

_logger = logging.getLogger("pty-session")

_SGR_MOUSE_RE_BYTES = re.compile(rb'\x1b\[<(\d+);(\d+);(\d+)([Mm])')
_SGR_MOUSE_RE_STR = re.compile(r'\x1b\[<(\d+);(\d+);(\d+)([Mm])')


class InputInterceptor:
    """输入拦截器 — 处理 SGR 鼠标、键盘 VT 序列和鼠标动作

    Args:
        pty_provider:  返回当前 PTY 实例的可调用对象。
        event_sink:    事件接收回调（EventHistoryManager.add_event）。
        cols:          终端列数。
        rows:          终端行数。
    """

    def __init__(
        self,
        pty_provider: Callable,
        event_sink: Callable,
        cols: int,
        rows: int,
    ):
        self._pty_provider = pty_provider
        self._event_sink = event_sink
        self._cols = cols
        self._rows = rows
        self._app_mouse_mode = False
        self._last_mouse_press = None

    def resize(self, cols: int, rows: int):
        self._cols, self._rows = cols, rows

    # ════════════════════════════════════════════════════════════
    #  拦截入口
    # ════════════════════════════════════════════════════════════

    def intercept(self, data, child_encoding: Optional[str],
                  encoding: Optional[str], session_id: str):
        """拦截输入数据（编码转换 + SGR 鼠标 + 键盘 VT），返回处理后的数据

        Args:
            data:           原始输入数据（str 或 bytes）。
            child_encoding: 子进程编码设置。
            encoding:       当前探测到的编码。
            session_id:     会话 ID（用于日志）。

        Returns:
            处理后的数据。
        """
        input_encoding = child_encoding or encoding
        if isinstance(data, str) and input_encoding:
            enc_norm = input_encoding.lower().replace("-", "").replace("_", "")
            if enc_norm not in ("utf8", "utf"):
                _logger.debug("intercept: encoding=%s → encode input to %s",
                              input_encoding, input_encoding)
                data = data.encode(input_encoding, errors="replace")

        pty = self._pty_provider()

        sgr_mode = (os.environ.get('PTYAGENT_SGR_MODE') or 'inject').lower()
        if IS_WINDOWS and pty and hasattr(pty, 'inject_mouse_event') and sgr_mode == 'inject':
            data = self._intercept_sgr_mouse(data, pty, session_id)
        elif IS_WINDOWS and sgr_mode == 'pipe':
            if isinstance(data, bytes):
                if b'\x1b[<' in data:
                    _logger.info("intercept: SGR pipe mode (passthrough) data=%r sid=%s",
                                 data[:80], session_id)
            elif isinstance(data, str) and '\x1b[<' in data:
                _logger.info("intercept: SGR pipe mode (passthrough) data=%r sid=%s",
                             data[:80], session_id)

        if IS_WINDOWS and pty and hasattr(pty, 'inject_key_events') and hasattr(pty, '_vt_sequence_to_key_records'):
            data = self._intercept_keyboard_vt(data, pty, session_id)

        return data

    # ════════════════════════════════════════════════════════════
    #  SGR 鼠标拦截
    # ════════════════════════════════════════════════════════════

    def _intercept_sgr_mouse(self, data, pty, session_id: str):
        """处理 SGR 鼠标序列：根据子进程输入模式选择最佳路径

        Windows ConPTY 鼠标注入策略（基于 tcell/OpenTUI/MiMo 行为分析）：

        子进程输入模式决定注入路径：
        - VT_INPUT=ON（MiMo/OpenTUI 等 Node.js TUI）：子进程通过 ReadFile(stdin)
          读取 VT 序列，不使用 ReadConsoleInputW。必须将 SGR 序列作为 KEY_EVENT_RECORD
          注入（inject_vt_bytes），conhost 会将 KEY_EVENT_RECORD 翻译为 VT 序列通过
          stdin 送达子进程。MOUSE_EVENT_RECORD 在 VT_INPUT 模式下不会被翻译为 VT 序列。
        - VT_INPUT=OFF（tcell/gdu 等 Go TUI）：子进程通过 ReadConsoleInputW 读取
          MOUSE_EVENT_RECORD。使用 inject_mouse_events 直接写入 MOUSE_EVENT_RECORD。

        不通过 ConPTY 管道透传 SGR：系统 conhost（Win10 22H2）在 VT_INPUT 模式下
        会丢弃 SGR 序列（ActionCsiDispatch return false）。

        Args:
            data: 原始输入数据（str 或 bytes）。
            pty:  当前 PTY 实例。
            session_id: 会话 ID。

        Returns:
            处理后的数据（移除已通过 inject 处理的 SGR 序列，保留非鼠标数据）。
        """
        is_str = isinstance(data, str)
        pattern = _SGR_MOUSE_RE_STR if is_str else _SGR_MOUSE_RE_BYTES
        release_marker = 'm' if is_str else b'm'
        empty = '' if is_str else b''

        matches = list(pattern.finditer(data))
        if not matches:
            return data

        sample = matches[0].group(0)
        if len(sample) > 60:
            sample = sample[:60]

        vt_input = hasattr(pty, 'is_vt_input_enabled') and pty.is_vt_input_enabled()
        _logger.info("intercept: SGR mouse events=%d sample=%r vt_input=%s sid=%s",
                     len(matches), sample, vt_input, session_id)

        has_inject_mouse = hasattr(pty, 'inject_mouse_event')
        has_inject_vt = hasattr(pty, 'inject_vt_bytes')

        if has_inject_mouse and matches:
            # 统一走 inject_mouse_events 路径，不再区分 vt_input=ON/OFF。
            # 原分支：vt_input=ON 走 inject_vt_bytes（KEY_EVENT_RECORD），
            #         vt_input=OFF 走 inject_mouse_events（MOUSE_EVENT_RECORD）。
            # 问题：conhost 在 VT_INPUT 模式下会丢弃通过 KEY_EVENT_RECORD 注入的
            #       SGR 序列（ActionCsiDispatch return false），inject_vt_bytes
            #       路径下子进程根本收不到 SGR 字节。
            # 修复：vt_input=ON 时也走 inject_mouse_events，conhost 的
            #       InputStateMachineEngine 会把 MOUSE_EVENT_RECORD 翻译为
            #       SGR 1006 字节流通过 stdin 送达子进程。前提是 conhost 自己
            #       启用了 ?1006 鼠标模式——由 mediator 在子进程启用 VT_INPUT
            #       时发送 [?1002h[?1006h 到 ConPTY 输入端启用
            #       （见 Mediator.cpp:408 OnModeChange）。
            events = []
            for m in matches:
                button = int(m.group(1))
                col = int(m.group(2))
                row = int(m.group(3))
                is_release = m.group(4) == release_marker
                x = col - 1
                y = row - 1
                events.append((x, y, button, is_release))

            enhanced_events = []
            prev_press = self._last_mouse_press
            for ev in events:
                x, y, button, is_release = ev[0], ev[1], ev[2], ev[3]
                is_wheel = (button & 0x40) != 0
                is_drag = (button & 0x20) != 0
                base_button = button & 0x03
                double_click = False

                if not is_release and not is_wheel and not is_drag and base_button != 3:
                    if prev_press == (x, y, base_button):
                        double_click = True
                    prev_press = (x, y, base_button)
                elif is_release:
                    pass
                else:
                    prev_press = None

                enhanced_events.append((x, y, button, is_release, double_click))

            self._last_mouse_press = prev_press

            try:
                if hasattr(pty, 'inject_mouse_events'):
                    ok = pty.inject_mouse_events(enhanced_events)
                    _logger.info("intercept: inject_mouse_events (vt_input=%s) batch=%d ok=%s sid=%s",
                                 vt_input, len(enhanced_events), ok, session_id)
                else:
                    ok = True
                    for ev in enhanced_events:
                        x, y, button, is_release = ev[0], ev[1], ev[2], ev[3]
                        ok = pty.inject_mouse_event(x, y, button, is_release) and ok
                    _logger.info("intercept: inject_mouse_event (loop, vt_input=%s) count=%d ok=%s sid=%s",
                                 vt_input, len(enhanced_events), ok, session_id)
            except Exception as e:
                _logger.warning("inject_mouse_events 失败: sid=%s count=%d err=%s",
                                session_id, len(events), e)

        result = pattern.sub(empty, data)
        _logger.info("intercept: mouse injected (vt_input=%s), SGR removed from data sid=%s",
                     vt_input, session_id)
        return result

    # ════════════════════════════════════════════════════════════
    #  键盘 VT 拦截
    # ════════════════════════════════════════════════════════════

    def _intercept_keyboard_vt(self, data, pty, session_id: str):
        """拦截键盘 CSI/SS3 序列并通过 inject_key_events 注入

        系统conhost在VT_INPUT模式下丢弃所有CSI序列（ActionCsiDispatch return false），
        导致方向键、功能键等键盘VT序列无法到达子进程。此方法将键盘VT序列解析为
        KEY_EVENT_RECORD 并通过 WriteConsoleInputW 直接注入子进程控制台输入缓冲区。

        仅在子进程启用了 ENABLE_VIRTUAL_TERMINAL_INPUT 时才拦截。

        Args:
            data: 原始输入数据（str 或 bytes）。
            pty:  当前 PTY 实例。
            session_id: 会话 ID。

        Returns:
            处理后的数据（移除已注入的键盘VT序列，保留其他数据）。
        """
        vt_input = hasattr(pty, 'is_vt_input_enabled') and pty.is_vt_input_enabled()
        if not vt_input:
            return data

        is_str = isinstance(data, str)
        raw = data.encode('utf-8') if is_str else data

        if b'\x1b' not in raw:
            return data

        parsed = pty._vt_sequence_to_key_records(raw)
        if not parsed:
            return data

        key_specs = []
        spans_to_remove = []
        for start, end, vk, sc, wch, mod_state in parsed:
            key_specs.append((vk, sc, wch, mod_state))
            spans_to_remove.append((start, end))

        if not key_specs:
            return data

        try:
            ok = pty.inject_key_events(key_specs)
            _logger.info("intercept: keyboard VT injected keys=%d ok=%s sid=%s",
                         len(key_specs), ok, session_id)
        except Exception as e:
            _logger.warning("inject_key_events 失败: sid=%s keys=%d err=%s",
                            session_id, len(key_specs), e)
            return data

        result = bytearray(raw)
        for start, end in reversed(spans_to_remove):
            del result[start:end]
        result = bytes(result)

        if is_str:
            result = result.decode('utf-8', errors='replace')

        return result if result else ('' if is_str else b'')

    # ════════════════════════════════════════════════════════════
    #  鼠标动作执行
    # ════════════════════════════════════════════════════════════

    def perform_mouse_action(self, action: dict, screen, pty_type: str,
                             session_id: str, running: bool,
                             write_fn: Callable) -> dict:
        """执行鼠标动作

        Args:
            action:     描述动作的字典，含 action/coords/button/count/...
            screen:     TerminalScreen 实例。
            pty_type:   当前 PTY 类型。
            session_id: 会话 ID。
            running:    会话是否正在运行。
            write_fn:   写入输入的回调函数（用于 write_input）。

        Returns:
            {"performed": bool, "matches": [...], "message": "..."}
        """
        if not running:
            return {"performed": False, "message": f"Session '{session_id}' is not running"}

        action_type = action.get("action", "")

        if action_type == "_get_cursor_location":
            loc = screen.get_cursor_location()
            return {"performed": True, "cursor": loc}

        encoder = MouseActionEncoder(self._cols, self._rows)
        coords = action.get("coords")
        grep = action.get("grep")

        if coords is None and grep:
            try:
                matches = grep_screen(screen, grep)
            except MouseError as e:
                return {"performed": False, "message": str(e)}
            if not matches:
                return {"performed": False, "matches": [], "message": "No match found"}
            if len(matches) > 1:
                return {
                    "performed": False,
                    "matches": [m.as_dict() for m in matches],
                    "message": "Multiple matches found; please specify coordinates or a more specific pattern",
                }
            coords = {"col": matches[0].start.col, "row": matches[0].start.row}
        elif coords is None:
            return {"performed": False, "message": "Missing coordinates or --grep pattern"}

        from_coord = Coord(coords["col"], coords["row"])
        modifiers = sorted(set(action.get("modifiers", [])))

        try:
            if action_type == "click":
                ops = encoder.click(
                    from_coord,
                    action.get("button", "left"),
                    action.get("count", 1),
                    modifiers,
                )
            elif action_type == "hover":
                ops = encoder.hover(from_coord, modifiers)
            elif action_type == "scroll":
                ops = encoder.scroll(
                    from_coord,
                    action.get("direction", "up"),
                    action.get("times", 1),
                    modifiers,
                )
            elif action_type == "drag":
                to = action.get("to")
                if not to:
                    return {"performed": False, "message": "drag requires destination coordinates"}
                to_coord = Coord(to["col"], to["row"])
                ops = encoder.drag(
                    from_coord,
                    to_coord,
                    action.get("button", "left"),
                    modifiers,
                )
            elif action_type == "press":
                ops = encoder.press(
                    from_coord,
                    action.get("button", "left"),
                    action.get("duration", 1.0),
                    modifiers,
                )
            elif action_type == "grep":
                try:
                    matches = grep_screen(screen, grep)
                except MouseError as e:
                    return {"performed": False, "message": str(e)}
                return {"performed": False, "matches": [m.as_dict() for m in matches]}
            else:
                return {"performed": False, "message": f"Unknown mouse action: {action_type}"}
        except MouseError as e:
            return {"performed": False, "message": str(e)}

        for op in ops:
            if op["type"] == "write":
                write_fn(op["data"])
            elif op["type"] == "sleep":
                time.sleep(op["duration"])

        return {"performed": True}

    # ════════════════════════════════════════════════════════════
    #  控制台鼠标模式轮询
    # ════════════════════════════════════════════════════════════

    def update_mouse_mode_from_console(self, session_id: str, running: bool):
        """通过子进程控制台输入模式检测是否需要鼠标事件

        某些 Windows TUI（如基于 OpenTUI 的 MiMo）不通过 stdout 发送 DECSET
        鼠标序列，而是直接设置控制台 ENABLE_MOUSE_INPUT + 原始输入模式。
        此方法轮询子进程控制台模式，当状态变化时通过 event_history 通知前端。
        """
        _logger.debug("update_mouse_mode_from_console: sid=%s is_windows=%s running=%s",
                      session_id, IS_WINDOWS, running)
        if not IS_WINDOWS or not running:
            return
        pty = self._pty_provider()
        if not pty or not hasattr(pty, 'is_tui_mouse_input_enabled'):
            _logger.debug("update_mouse_mode_from_console: sid=%s pty unavailable or no is_tui_mouse_input_enabled", session_id)
            return
        try:
            wants = pty.is_tui_mouse_input_enabled()
        except Exception as e:
            _logger.info("update_mouse_mode_from_console: detect failed sid=%s: %s",
                         session_id, e)
            return
        _logger.info("update_mouse_mode_from_console: sid=%s wants=%s prev=%s",
                     session_id, wants, self._app_mouse_mode)
        if wants == self._app_mouse_mode:
            return
        self._app_mouse_mode = wants
        try:
            self._event_sink(PendingEvent(
                timestamp=time.time(),
                type='mouse_mode',
                detail={'enabled': bool(wants)},
            ))
            _logger.info("mouse_mode event queued: sid=%s enabled=%s", session_id, wants)
        except Exception:
            _logger.warning("mouse_mode event queue failed: sid=%s", session_id, exc_info=True)
        _logger.info("mouse_mode changed: sid=%s enabled=%s", session_id, wants)

    @property
    def app_mouse_mode(self) -> bool:
        return self._app_mouse_mode
