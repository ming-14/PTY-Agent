"""终端界面渲染：棋盘、得分、状态与按键提示。

使用 ANSI 256 色转义序列，Windows 现代终端与 Linux 终端均支持。
"""

from __future__ import annotations

import os
import sys
from typing import List

from . import debuglog, logic, sprites

# 开启 Windows 控制台的 ANSI 转义支持（对不支持的环境无副作用）
if os.name == "nt":
    os.system("")

CLEAR_SCREEN = "\033[2J\033[H"
_RESET = "\033[0m"

# 替代屏幕（alternate screen buffer）：进入后独占终端，退出时恢复原画面，不污染 scrollback
ALT_SCREEN_ENTER = "\033[?1049h"
ALT_SCREEN_EXIT = "\033[?1049l"
# 隐藏/显示光标（游戏进行中不显示闪烁光标）
_CURSOR_HIDE = "\033[?25l"
_CURSOR_SHOW = "\033[?25h"
# 开启鼠标上报（含 SGR 坐标 + button-event tracking 拖动）：
# ?1000 X10 按下/松开；?1002 拖动(按住移动)；?1006 SGR 坐标。
# 缺少 ?1002 时终端不会在拖动中途发送 motion 事件，奖杯将无法跟随指针。
_MOUSE_ENABLE = "\033[?1000h\033[?1002h\033[?1006h"
_MOUSE_DISABLE = "\033[?1000l\033[?1002l\033[?1006l"

# 棋盘左上角画布坐标（0 基）
BOARD_ROW = 2
BOARD_COL = 2

# 各数字块的 (背景色, 前景色)，256 色索引
_TILE_COLORS = {
    0: (235, 245),
    2: (229, 16),
    4: (223, 16),
    8: (215, 16),
    16: (214, 16),
    32: (208, 231),
    64: (202, 231),
    128: (178, 231),
    256: (172, 231),
    512: (166, 231),
    1024: (160, 231),
    2048: (128, 231),
}
_OTHER_COLOR = (93, 231)  # 超过 2048 的块

# 退出提示用 "exit" 而非 "quit"：exec 以 "quit" 作 trigger 匹配输出，
# 提示文案不得包含该子串（否则首帧渲染即误触发返回）
_HINT_PLAYING = "↑/↓/←/→  R restart  Q exit"
_HINT_TROPHY = "Drag the golden trophy into the cabinet  (C to dismiss)"
_STATUS_TEXT = {
    logic.WON: "{} reached! Keep going!  (R restart / Q exit)",
    logic.LOST: "Game over!  (R restart / Q exit)",
}


# --------------------------------------------------------------------- #
# 几何：画布 / 格子 / 奖杯 / 成就柜 的坐标计算与命中测试                  #
# --------------------------------------------------------------------- #
def tile_width(game: "logic.Game") -> int:
    """单个格子显示宽度（列）。"""
    return max(4, len(str(max(max(row) for row in game.board))))


def canvas_size(game: "logic.Game", has_trophy: bool = True, hint: str = None):
    """画布尺寸 (行, 列)。

    has_trophy: 是否显示奖杯/成就柜（为 False 时省略奖杯区，提示行紧贴棋盘）。
    hint: 底部提示文案；超宽时画布自动加宽，保证提示完整显示。
    """
    w = tile_width(game)
    extra = sprites.TROPHY_H + 2 if has_trophy else 3
    rows = BOARD_ROW + 2 * game.size + extra  # 棋盘 + 底部提示
    board_w = 1 + game.size * (w + 1)
    cols = BOARD_COL + board_w
    if has_trophy:
        _, cw, _ = sprites.cabinet(False)
        cols += sprites.CABINET_GAP + cw
    if hint:
        cols = max(cols, len(hint))
    return rows, cols


def cell_pos(game: "logic.Game", r: int, c: int):
    """格子内容区左上角 (行, 列)，用于奖杯初始定位与命中。"""
    w = tile_width(game)
    row = BOARD_ROW + 2 * r + 1
    col = BOARD_COL + 1 + c * (w + 1)
    return row, col


def cabinet_rect(game: "logic.Game"):
    """成就柜矩形 (row, col, h, w)。"""
    w = tile_width(game)
    board_w = 1 + game.size * (w + 1)
    col = BOARD_COL + board_w + sprites.CABINET_GAP
    _, cw, ch = sprites.cabinet(False)
    return BOARD_ROW, col, ch, cw


def trophy_rect(trophy: dict):
    """奖杯矩形 (row, col, h, w)。"""
    return trophy["row"], trophy["col"], sprites.TROPHY_H, sprites.TROPHY_W


def point_in_rect(row: int, col: int, rect) -> bool:
    r0, c0, h, w = rect
    return r0 <= row < r0 + h and c0 <= col < c0 + w


def rects_overlap(a, b) -> bool:
    ar, ac, ah, aw = a
    br, bc, bh, bw = b
    return not (ar + ah <= br or br + bh <= ar or ac + aw <= bc or bc + bw <= ac)


def spawn_trophy(game: "logic.Game") -> dict:
    """在棋盘下方居中生成奖杯状态（可拖动）。

    2048 格仅 4 列宽，放不下 12 列宽的奖杯，
    故把奖杯生成在棋盘正下方居中的空白区，便于玩家观察与拖拽。
    """
    board_bottom = BOARD_ROW + 2 * game.size  # 棋盘底线的行号
    row = board_bottom + 1
    board_w = 1 + game.size * (tile_width(game) + 1)
    col = BOARD_COL + max(0, (board_w - sprites.TROPHY_W) // 2)
    return {"row": row, "col": col,
            "held": False, "grab_r": 0, "grab_c": 0}


# --------------------------------------------------------------------- #
# 渲染                                                                    #
# --------------------------------------------------------------------- #
def render_simple(game: "logic.Game") -> None:
    """简单模式渲染：清屏后只输出棋盘纯文本（空位 '-'），无任何其他元素。"""
    sys.stdout.write(CLEAR_SCREEN + board_text(game) + "\n")
    sys.stdout.flush()


def board_text(game: "logic.Game") -> str:
    """棋盘纯文本：每行数字以空格分隔，空位用 '-' 表示。"""
    return "\n".join(
        " ".join(str(v) if v else "-" for v in row)
        for row in game.board
    )


# simple 呈现模式：开启后 render() 只输出棋盘纯文本（其余元素全部忽略）
_simple = False


def set_simple(enabled: bool) -> None:
    """切换 CLI 呈现模式：simple 时 render() 只输出棋盘纯文本。

    仅影响呈现层输出；控制台生命周期、游戏逻辑与普通模式完全一致。
    """
    global _simple
    _simple = enabled


def simple() -> bool:
    """是否处于 simple 呈现模式（供主循环跳过奖杯阶段等判断）。"""
    return _simple


def init() -> None:
    """进入替代屏幕、开启鼠标上报、设为原始输入并隐藏光标。"""
    _setup_windows_console(enter=True)
    sys.stdout.write(ALT_SCREEN_ENTER + _MOUSE_ENABLE + CLEAR_SCREEN + _CURSOR_HIDE)
    sys.stdout.flush()


def cleanup() -> None:
    """退出前恢复光标、关闭鼠标上报、恢复控制台模式并离开替代屏幕。"""
    if _WIN_ORIG_MODE is not None:
        _setup_windows_console(enter=False)
    sys.stdout.write(_CURSOR_SHOW + CLEAR_SCREEN + ALT_SCREEN_EXIT + _MOUSE_DISABLE + _RESET)
    sys.stdout.flush()


# Windows 输入控制台模式的原值（用于退出时恢复）
_WIN_ORIG_MODE = None

# 控制台输入模式标志
_ENABLE_PROCESSED_INPUT = 0x0001
_ENABLE_LINE_INPUT = 0x0002
_ENABLE_ECHO_INPUT = 0x0004
_ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200


def _setup_windows_console(enter: bool) -> None:
    """Windows 下切换控制台输入模式。

    enter=True 进入"原始输入"模式：关闭行缓冲(LINE_INPUT)与回显(ECHO_INPUT)，
    保留 Ctrl+C 处理(PROCESSED_INPUT) 并开启 VT 输入(VIRTUAL_TERMINAL_INPUT)。
    这样 os.read 才能尽快返回方向键/鼠标 VT 字节，且不回显到屏幕。
    enter=False 时恢复进入前的原始模式。
    """
    global _WIN_ORIG_MODE
    if os.name != "nt":
        return
    try:
        import ctypes

        _STD_INPUT_HANDLE = -10
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(_STD_INPUT_HANDLE)
        if handle == -1 or handle == 0:
            debuglog.log("console: GetStdHandle failed/not-console handle={}".format(handle))
            return
        mode = ctypes.c_uint32()
        got = kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        debuglog.log("console: enter={} GetConsoleMode_ok={} old_mode={:#x}".format(
            bool(enter), bool(got), mode.value))
        if not got:
            return
        if enter:
            _WIN_ORIG_MODE = mode.value
            new_mode = (mode.value
                        & ~(_ENABLE_LINE_INPUT | _ENABLE_ECHO_INPUT)
                        | _ENABLE_PROCESSED_INPUT
                        | _ENABLE_VIRTUAL_TERMINAL_INPUT)
            ok = kernel32.SetConsoleMode(handle, new_mode)
            debuglog.log("console: set_mode_ok={} new_mode={:#x} (vt_input_on={})".format(
                bool(ok), new_mode, bool(new_mode & _ENABLE_VIRTUAL_TERMINAL_INPUT)))
        else:
            ok = kernel32.SetConsoleMode(handle, _WIN_ORIG_MODE)
            debuglog.log("console: restore_mode_ok={} restore={:#x}".format(
                bool(ok), _WIN_ORIG_MODE))
            _WIN_ORIG_MODE = None
    except Exception as e:  # noqa: BLE001
        debuglog.log("console: exception {}".format(e))
        pass  # 非真实控制台（重定向等）时静默忽略


def render(game: "logic.Game", status: str = None, best: int = 0,
           trophy: dict = None, anim: int = None, achieved: bool = False,
           over_cabinet: bool = False) -> None:
    """在替代屏幕内清屏并渲染一帧画面。

    simple 呈现模式下忽略全部附加元素，只输出棋盘纯文本。
    """
    if _simple:
        render_simple(game)
        return
    sys.stdout.write(CLEAR_SCREEN + _build_screen(game, status, best,
                                                  trophy, anim, achieved, over_cabinet))
    sys.stdout.flush()


def _build_screen(game: "logic.Game", status: str = None, best: int = 0,
                  trophy: dict = None, anim: int = None, achieved: bool = False,
                  over_cabinet: bool = False) -> str:
    status = status or game.status()
    hint = _pick_hint(status, game, trophy, achieved)
    rows, cols = canvas_size(game, has_trophy=(trophy is not None or achieved),
                             hint=hint)
    cv = _Canvas(rows, cols)
    w = tile_width(game)

    # 顶部：得分
    cv.text(0, 0, "Score: {}   Best: {}".format(game.score, max(best, game.score)))
    cv.text(0, cols - 1, " ")  # 占位，保持画布宽度一致

    # 棋盘
    seg = "─" * w
    _draw_line(cv, BOARD_ROW, BOARD_COL, "┌", "┬", "┐", seg, game.size)
    for r in range(game.size):
        row = BOARD_ROW + 1 + 2 * r
        for c in range(game.size):
            v = game.board[r][c]
            style = _tile_style(v)
            text = str(v).center(w) if v else " " * w
            cv.text(row, BOARD_COL + 1 + c * (w + 1), text, style)
        # 竖线：格子间分隔列 + 左右边框（与横线的 ┬┼┴ 接头对齐）
        for c in range(game.size + 1):
            cv.put(row, BOARD_COL + c * (w + 1), "\u2502")
        if r < game.size - 1:
            _draw_line(cv, row + 1, BOARD_COL, "├", "┼", "┤", seg, game.size)
    _draw_line(cv, BOARD_ROW + 1 + 2 * (game.size - 1) + 1, BOARD_COL,
               "└", "┴", "┘", seg, game.size)

    # 成就柜（奖杯阶段显示）
    if trophy is not None or achieved:
        _draw_cabinet(cv, game, filled=achieved, glow=over_cabinet)

    # 奖杯精灵
    if trophy is not None and (anim is None or anim >= 2):
        _draw_trophy(cv, trophy)

    # 2048 格变身动画：金色闪光
    if anim is not None and anim < 2:
        _draw_anim(cv, game, anim)

    # 底部提示
    cv.text(rows - 1, 0, hint)

    return "\n".join(cv.render_rows()) + "\n"


def _pick_hint(status: str, game: "logic.Game", trophy: dict,
               achieved: bool) -> str:
    """选择底部提示文案（奖杯阶段优先，其次按游戏状态）。"""
    if trophy is not None and not achieved:
        return _HINT_TROPHY
    if status == logic.WON:
        return _STATUS_TEXT[logic.WON].format(game.win_value)
    return _STATUS_TEXT.get(status, _HINT_PLAYING)


def _draw_line(cv, row, col, left, mid, right, seg, n) -> None:
    cv.text(row, col, left + mid.join([seg] * n) + right)


def _draw_trophy(cv, trophy: dict) -> None:
    """绘制奖杯为透明浮层：只画非空格字素，空格透出下层棋盘。"""
    r, c = trophy["row"], trophy["col"]
    lines = sprites.trophy()
    for dr, line in enumerate(lines):
        style = (sprites.TROPHY_ROW_COLOR[dr]
                 if dr < len(sprites.TROPHY_ROW_COLOR) else sprites.GOLD)
        for dc, ch in enumerate(line):
            if ch != " ":  # 空格透明，不覆盖下层
                cv.put(r + dr, c + dc, ch, style)


def _draw_cabinet(cv, game, filled: bool, glow: bool) -> None:
    r, c, _, _ = cabinet_rect(game)
    rows, _, _ = sprites.cabinet(filled, glow, game.win_value)
    for dr, line in enumerate(rows):
        # 泛金边框：以 style 参数上色，而非把 ANSI 内嵌进字形（内嵌会
        # 被画布当作字面字符逐格存放，拖拽覆盖时产生裸转义/乱码）
        style = sprites.GOLD if glow and dr in (0, len(rows) - 1) else None
        cv.text(r + dr, c, line, style)


def _draw_anim(cv, game, anim: int) -> None:
    """2048 格位置的金色闪烁。"""
    sparkle = ["*   *", " * * ", "  *  ", " * * "]
    for r in range(game.size):
        for c in range(game.size):
            if game.board[r][c] == game.win_value:
                row, col = cell_pos(game, r, c)
                w = tile_width(game)
                cv.text(row, col, str(game.win_value).center(w),
                        "\033[38;5;228m\033[1m")
                cv.put(row, col + w // 2, sparkle[anim % 4][w // 2], "\033[38;5;220m\033[1m")
                return


def _tile_style(value: int) -> str:
    bg, fg = _TILE_COLORS.get(value, _OTHER_COLOR)
    return "\033[38;5;{}m\033[48;5;{}m".format(fg, bg)


# --------------------------------------------------------------------- #
# 字符画布                                                                #
# --------------------------------------------------------------------- #
class _Canvas:
    """字符画布：每格一个字符 + 可选 ANSI 样式，支持按位置覆盖写。"""

    def __init__(self, rows: int, cols: int):
        self.rows = rows
        self.cols = cols
        self.cells = [[" "] * cols for _ in range(rows)]
        self.styles = [[None] * cols for _ in range(rows)]

    def put(self, row: int, col: int, ch: str, style: str = None) -> None:
        if 0 <= row < self.rows and 0 <= col < self.cols:
            self.cells[row][col] = ch
            self.styles[row][col] = style

    def text(self, row: int, col: int, s: str, style: str = None) -> None:
        for i, ch in enumerate(s):
            self.put(row, col + i, ch, style)

    def render_rows(self) -> List[str]:
        return [self._render_row(r) for r in range(self.rows)]

    def _render_row(self, r: int) -> str:
        out = []
        cur = None
        for c in range(self.cols):
            ch = self.cells[r][c]
            st = self.styles[r][c]
            if st != cur:
                if cur is not None:
                    out.append(_RESET)
                if st is not None:
                    out.append(st)
                cur = st
            out.append(ch)
        if cur is not None:
            out.append(_RESET)
        return "".join(out).rstrip()
