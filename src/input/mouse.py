"""鼠标动作编码与屏幕坐标解析

提供 SGR 鼠标序列生成以及基于终端屏幕快照的 grep 坐标定位。
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..config.common import DEFAULT_COLS, DEFAULT_ROWS
from ..terminal.screen import TerminalScreen


class MouseError(ValueError):
    """鼠标动作参数错误"""


@dataclass(frozen=True)
class Coord:
    """终端坐标（1-based，与 SGR 鼠标协议一致）"""

    col: int
    row: int

    def as_dict(self) -> dict:
        return {"col": self.col, "row": self.row}


@dataclass(frozen=True)
class MatchRegion:
    """grep 匹配区域（首坐标 / 尾坐标）"""

    start: Coord
    end: Coord

    def as_dict(self) -> dict:
        return {"start": self.start.as_dict(), "end": self.end.as_dict()}


_BUTTON_MAP = {"left": 0, "middle": 1, "right": 2}
_MODIFIER_MAP = {"shift": 4, "alt": 8, "ctrl": 16}


def _encode_button(
    button: str,
    modifiers: List[str],
    motion: bool = False,
) -> int:
    """编码 SGR 按钮值

    SGR-1006 鼠标协议 button 编码（参考 WT _windowsButtonToSGREncoding）：
        bits 0-1: 0=left, 1=middle, 2=right, 3=hover（无按键移动）
        bit  2  (0x04): shift
        bit  3  (0x08): alt
        bit  4  (0x10): ctrl
        bit  5  (0x20): motion/drag
        bit  6  (0x40): wheel — 0x40=up, 0x41=down, 0x42=left, 0x43=right

    注意：SGR-1006 中 release 与 press 使用相同的 button 值，
    仅通过 M/m 标记区分，不使用旧 X10 协议的 button=3 表示 release。
    """
    if button == "scroll_up":
        value = 64          # 0x40, SGR wheel up
    elif button == "scroll_down":
        value = 65          # 0x41, SGR wheel down
    else:
        value = _BUTTON_MAP[button]
    for m in modifiers:
        flag = _MODIFIER_MAP.get(m)
        if flag is not None:
            value |= flag
    if motion:
        value |= 32
    return value


def _sgr_sequence(col: int, row: int, button_value: int, is_release: bool) -> bytes:
    """生成单条 SGR 鼠标序列（坐标 1-based）"""
    marker = "m" if is_release else "M"
    return f"\x1b[<{button_value};{col};{row}{marker}".encode("utf-8")


def _bresenham_line(start: Coord, end: Coord) -> List[Coord]:
    """Bresenham 直线算法，返回从起点到终点的所有坐标（含端点）"""
    x0, y0 = start.col, start.row
    x1, y1 = end.col, end.row
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    points: List[Coord] = []
    while True:
        points.append(Coord(col=x0, row=y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return points


class MouseActionEncoder:
    """SGR 鼠标动作编码器"""

    def __init__(self, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS):
        self.cols = cols
        self.rows = rows

    def _validate(self, coord: Coord) -> None:
        if not (1 <= coord.col <= self.cols):
            raise MouseError(f"col {coord.col} out of range [1, {self.cols}]")
        if not (1 <= coord.row <= self.rows):
            raise MouseError(f"row {coord.row} out of range [1, {self.rows}]")

    def click(
        self,
        coord: Coord,
        button: str,
        count: int,
        modifiers: List[str],
    ) -> List[dict]:
        """生成单击/双击/三击操作序列

        关键：每次 click 的 press+release 必须作为单次 write 发送，这样
        _intercept_sgr_mouse 才能把它们批量注入到同一次 inject_mouse_events
        调用中。如果 press 和 release 分别 write，会触发两次独立的
        AttachConsole + VT_INPUT 切换周期，tcell 可能在 VT_INPUT 恢复后才
        读取部分事件，导致 tview 的 fireMouseActions 无法识别 click 序列。

        双击/三击时，第二次及后续的 press 事件标记 double_click=True，
        inject_mouse_events 会据此在 dwEventFlags 中设置 DOUBLE_CLICK 位。
        Windows TUI 程序（tcell/tview/gdu）依赖此标志识别双击。
        """
        self._validate(coord)
        if button not in _BUTTON_MAP:
            raise MouseError(f"unknown button: {button}")
        if count not in (1, 2, 3):
            raise MouseError(f"click count must be 1/2/3, got {count}")
        ops: List[dict] = []
        btn_value = _encode_button(button, modifiers)
        for i in range(count):
            sgr_data = (
                _sgr_sequence(coord.col, coord.row, btn_value, False)
                + _sgr_sequence(coord.col, coord.row, btn_value, True)
            )
            double_click = i >= 1
            ops.append({"type": "write", "data": sgr_data, "double_click": double_click})
            if i < count - 1:
                ops.append({"type": "sleep", "duration": 0.05})
        return ops

    def hover(self, coord: Coord, modifiers: List[str]) -> List[dict]:
        """生成悬停（移动）操作序列

        WT 行为：纯 hover（无按键移动）的 SGR 编码为 button=3 + M（press 标记），
        不加 motion 标志(0x20)。只有拖拽（有按键按下的移动）才加 motion 标志。
        """
        self._validate(coord)
        value = 3
        for m in modifiers:
            flag = _MODIFIER_MAP.get(m)
            if flag is not None:
                value |= flag
        return [{"type": "write", "data": _sgr_sequence(coord.col, coord.row, value, False)}]

    def scroll(
        self,
        coord: Coord,
        direction: str,
        times: int,
        modifiers: List[str],
    ) -> List[dict]:
        """生成滚轮滚动操作序列

        WT 行为：滚轮事件只有 press（M），没有 release（m）。
        注入 MOUSE_EVENT_RECORD 时，滚轮的 dwEventFlags=MOUSE_WHEELED，
        dwButtonState 高16位为 WHEEL_DELTA（正=上，负=下）。
        """
        self._validate(coord)
        if direction not in ("up", "down"):
            raise MouseError(f"scroll direction must be up/down, got {direction}")
        if times < 1:
            raise MouseError(f"scroll times must be >= 1, got {times}")
        btn = "scroll_up" if direction == "up" else "scroll_down"
        ops: List[dict] = []
        btn_value = _encode_button(btn, modifiers)
        for _ in range(times):
            sgr_data = _sgr_sequence(coord.col, coord.row, btn_value, False)
            ops.append({"type": "write", "data": sgr_data})
        return ops

    def drag(
        self,
        from_coord: Coord,
        to_coord: Coord,
        button: str,
        modifiers: List[str],
    ) -> List[dict]:
        """生成拖拽操作序列（逐格移动）

        同 click，press + 所有 motion + release 合并为单次 write 以确保
        批量注入到同一 AttachConsole 周期。
        """
        self._validate(from_coord)
        self._validate(to_coord)
        if button not in _BUTTON_MAP:
            raise MouseError(f"unknown button: {button}")
        ops: List[dict] = []
        line = _bresenham_line(from_coord, to_coord)
        if not line:
            raise MouseError("drag path is empty")
        parts = []
        btn_value = _encode_button(button, modifiers)
        # 起点 press
        parts.append(_sgr_sequence(line[0].col, line[0].row, btn_value, False))
        # 中间移动（motion）
        motion_value = _encode_button(button, modifiers, motion=True)
        for coord in line[1:]:
            parts.append(_sgr_sequence(coord.col, coord.row, motion_value, False))
        # 终点 release
        parts.append(_sgr_sequence(line[-1].col, line[-1].row, btn_value, True))
        ops.append({"type": "write", "data": b"".join(parts)})
        return ops

    def press(
        self,
        coord: Coord,
        button: str,
        duration: float,
        modifiers: List[str],
    ) -> List[dict]:
        """生成长按操作序列

        注意：press+release 之间有 sleep（duration），无法合并为单次 write。
        tcell 在 VT_INPUT 恢复后读取事件可能导致部分丢失，但长按场景较少，
        且 duration 通常 > 100ms 远超 VT_INPUT 恢复时间，影响可忽略。
        """
        self._validate(coord)
        if button not in _BUTTON_MAP:
            raise MouseError(f"unknown button: {button}")
        if duration <= 0:
            raise MouseError(f"duration must be > 0, got {duration}")
        btn_value = _encode_button(button, modifiers)
        return [
            {"type": "write", "data": _sgr_sequence(coord.col, coord.row, btn_value, False)},
            {"type": "sleep", "duration": duration},
            {"type": "write", "data": _sgr_sequence(coord.col, coord.row, btn_value, True)},
        ]


def grep_screen(screen: TerminalScreen, pattern: str) -> List[MatchRegion]:
    """在终端屏幕快照上执行 grep，返回所有匹配区域的首/尾坐标

    坐标为 1-based，与 SGR 鼠标协议一致。
    匹配按行进行；每行可返回多个不重叠匹配。
    """
    try:
        pat = re.compile(pattern)
    except re.error as e:
        raise MouseError(f"Invalid regex: {e}")

    rows = screen.rows
    matches: List[MatchRegion] = []
    for row_idx in range(rows):
        line = screen.line_text(row_idx)
        for m in pat.finditer(line):
            # m.start() / m.end() 是 0-based 字符偏移；end 为开区间
            start_col = m.start() + 1
            end_col = m.end()
            matches.append(MatchRegion(
                start=Coord(col=start_col, row=row_idx + 1),
                end=Coord(col=end_col, row=row_idx + 1),
            ))
    return matches
