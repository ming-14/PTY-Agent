"""InputInterceptor — 输入处理辅助（编码转换 + 鼠标动作执行）

自 wezterm 模式感知编码接入后：
- 键盘/鼠标事件的编码由 WeztermInputEncoder 完成（session.key_input/mouse_input），
  编码字节直接写 pty，不再需要本类的 SGR 注入 / 键盘 VT 注入；
- 本类保留：write_input 的输入编码转换（str → 子进程编码字节）与
  鼠标动作执行（perform_mouse_action，CLI/daemon 特性，产生 SGR 字节直接写 pty）。
"""

import time
from typing import Callable, Optional

from .mouse import Coord, MouseActionEncoder, MouseError, grep_screen
from ..logging import get_logger

_logger = get_logger("pty-session")


class InputInterceptor:
    """输入处理辅助 — 编码转换与鼠标动作执行

    Args:
        cols: 终端列数。
        rows: 终端行数。
    """

    def __init__(
        self,
        cols: int,
        rows: int,
    ):
        self._cols = cols
        self._rows = rows

    def resize(self, cols: int, rows: int):
        self._cols, self._rows = cols, rows

    # ════════════════════════════════════════════════════════════
    #  编码转换（write_input 使用）
    # ════════════════════════════════════════════════════════════

    def intercept(
        self,
        data,
        child_encoding: Optional[str],
        encoding: Optional[str],
        session_id: str,
    ):
        """输入数据编码转换（str → 子进程编码字节），VT 序列原样透传

        键盘/鼠标事件由 wezterm 模式感知编码（WeztermInputEncoder）
        编码为 VT 序列并直接写 pty，OpenConsole 作为 VT 输入宿主
        将字节送达子进程 stdin。
        """
        input_encoding = child_encoding or encoding
        if isinstance(data, str) and input_encoding:
            enc_norm = input_encoding.lower().replace("-", "").replace("_", "")
            if enc_norm not in ("utf8", "utf"):
                _logger.debug(
                    "intercept: encoding=%s → encode input to %s",
                    input_encoding,
                    input_encoding,
                )
                data = data.encode(input_encoding, errors="replace")
        return data

    # ════════════════════════════════════════════════════════════
    #  鼠标动作执行
    # ════════════════════════════════════════════════════════════

    def perform_mouse_action(
        self,
        action: dict,
        screen,
        pty_type: str,
        session_id: str,
        running: bool,
        write_fn: Callable,
    ) -> dict:
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
            return {
                "performed": False,
                "message": f"Session '{session_id}' is not running",
            }

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
            return {
                "performed": False,
                "message": "Missing coordinates or --grep pattern",
            }

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
                    return {
                        "performed": False,
                        "message": "drag requires destination coordinates",
                    }
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
                return {
                    "performed": False,
                    "message": f"Unknown mouse action: {action_type}",
                }
        except MouseError as e:
            return {"performed": False, "message": str(e)}

        for op in ops:
            if op["type"] == "write":
                write_fn(op["data"])
            elif op["type"] == "sleep":
                time.sleep(op["duration"])

        return {"performed": True}
