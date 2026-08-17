"""终端模拟后端 —— wezterm-py 终端仿真引擎

终端模型统一抽象：可见区/scrollback 都表示为「行 × 单元格」的稀疏网格，
单元格是引擎原生元组 (col, data, fg, bg, bold, italic, underline, reverse,
strikethrough, width)，渲染函数直接按下标消费，避免逐 cell 构造中间对象。
渲染（纯文本 / 带 SGR 颜色 / 光标序列）在模块级共享，与具体引擎解耦。

实现：
- WeztermBackend：包装 pywezterm.Terminal（wezterm-term 终端模型），
  唯一后端，提供与 wezterm 完全一致的 VT 解析/光标/scrollback 语义。

TerminalScreen（screen.py）作为门面，通过 create_backend() 创建后端，
对外保持稳定 API，业务层不感知具体引擎。
"""

import functools
import os
import sys

from ..config.common import DEFAULT_COLS, DEFAULT_ROWS
from ..logging import get_logger

_logger = get_logger("pty-session")

# 加载 vendored pywezterm（bin/pywezterm，BUILD.ps1 编译产出），先注入 sys.path
_here = os.path.dirname(os.path.abspath(__file__))
_BIN_DIR = os.path.normpath(os.path.join(_here, *([os.path.pardir] * 2), "bin"))
_WEZTERM_PY_DIR = os.path.join(_BIN_DIR, "pywezterm")
if os.path.isdir(_WEZTERM_PY_DIR) and _WEZTERM_PY_DIR not in sys.path:
    sys.path.insert(0, _WEZTERM_PY_DIR)

try:
    import pywezterm

    _HAS_WEZTERM = True
except ImportError:
    _HAS_WEZTERM = False
    pywezterm = None  # type: ignore[assignment]

# scrollback 历史行上限（与 screen.py 保持一致：30000 行，过长输出按
# "万行尾缓冲"截断的问题缓解，前部历史仍由 100MB OutputBuffer 兜底）
_DEFAULT_HLIMIT = 30000

# 单元格元组字段下标（与 wezterm-py snapshot() 返回的 CellTuple 一致）：
# (col, data, fg, bg, bold, italic, underline, reverse, strikethrough, width)
_CELL_COL = 0
_CELL_DATA = 1
_CELL_FG = 2
_CELL_BG = 3
_CELL_BOLD = 4
_CELL_ITALIC = 5
_CELL_UNDERLINE = 6
_CELL_REVERSE = 7
_CELL_STRIKE = 8


# ── SGR 颜色序列化（与 grid.py / 旧 screen.py 逻辑一致，扩展 wezterm "pN" 调色板） ──

_ANSI_FG_NAMES = {
    "black": 0,
    "red": 1,
    "green": 2,
    "brown": 3,
    "blue": 4,
    "magenta": 5,
    "cyan": 6,
    "white": 7,
    "default": 9,
}
_ANSI_FG_BRIGHT = {
    "brightblack": 8,
    "brightred": 9,
    "brightgreen": 10,
    "brightbrown": 11,
    "brightblue": 12,
    "brightmagenta": 13,
    "brightcyan": 14,
    "brightwhite": 15,
}


@functools.lru_cache(maxsize=1024)
def color_to_sgr(color, is_fg: bool) -> str:
    """颜色值转 SGR 序列

    支持 wezterm 调色板索引、RGB hex（#rrggbb）、256 色索引（int）。
    常见调色板/样式组合结果缓存，避免逐 cell 重复拼接。
    """
    if color == "default":
        return ""
    prefix = "38" if is_fg else "48"
    # wezterm 调色板索引格式 "pN"（N ∈ 0-255，直接映射 256 色）
    if isinstance(color, str) and color.startswith("p") and color[1:].isdigit():
        return f"{prefix};5;{int(color[1:])}"
    if color in _ANSI_FG_NAMES:
        code = 30 + _ANSI_FG_NAMES[color] if is_fg else 40 + _ANSI_FG_NAMES[color]
        return str(code)
    if color in _ANSI_FG_BRIGHT:
        code = (
            90 + _ANSI_FG_BRIGHT[color] - 8
            if is_fg
            else 100 + _ANSI_FG_BRIGHT[color] - 8
        )
        return str(code)
    if isinstance(color, str):
        # wezterm 对 RGB 真彩色返回 "#rrggbb"（带 #，7 字符），同时兼容 6 位 hex
        hex_rgb = color[1:] if color.startswith("#") else color
        if len(hex_rgb) == 6:
            try:
                r = int(hex_rgb[0:2], 16)
                g = int(hex_rgb[2:4], 16)
                b = int(hex_rgb[4:6], 16)
                return f"{prefix};2;{r};{g};{b}"
            except ValueError:
                return ""
    if isinstance(color, int):
        return f"{prefix};5;{color}"
    return ""


def cell_to_sgr(cell) -> str:
    """将单元格属性转为 SGR 序列（样式组合结果缓存）"""
    return _style_to_sgr((cell[_CELL_BOLD], cell[_CELL_ITALIC], cell[_CELL_UNDERLINE],
                          cell[_CELL_REVERSE], cell[_CELL_STRIKE], cell[_CELL_FG], cell[_CELL_BG]))


@functools.lru_cache(maxsize=1024)
def _style_to_sgr(style) -> str:
    """按样式元组缓存 SGR 序列（终端内样式组合远少于 cell 数）"""
    bold, italic, underline, reverse, strikethrough, fg, bg = style
    attrs = []
    if bold:
        attrs.append("1")
    if italic:
        attrs.append("3")
    if underline:
        attrs.append("4")
    if reverse:
        attrs.append("7")
    if strikethrough:
        attrs.append("9")
    fg_sgr = color_to_sgr(fg, is_fg=True)
    if fg_sgr:
        attrs.append(fg_sgr)
    bg_sgr = color_to_sgr(bg, is_fg=False)
    if bg_sgr:
        attrs.append(bg_sgr)
    if not attrs:
        return "\x1b[0m"
    return f"\x1b[{';'.join(attrs)}m"


def is_default_cell(cell) -> bool:
    """是否默认空白单元格（无字符、无样式）"""
    return (
        (not cell[_CELL_DATA] or cell[_CELL_DATA] == " ")
        and (not cell[_CELL_FG] or cell[_CELL_FG] == "default")
        and (not cell[_CELL_BG] or cell[_CELL_BG] == "default")
        and not cell[_CELL_BOLD]
        and not cell[_CELL_ITALIC]
        and not cell[_CELL_UNDERLINE]
        and not cell[_CELL_REVERSE]
        and not cell[_CELL_STRIKE]
    )


def build_cursor_seq(x, y, visible) -> str:
    """构建光标定位 VT 序列（CSI Pl;Pc H 与显示/隐藏）

    坐标 0-based 输入，输出 1-based（与 xterm.js CPR 一致）。
    光标不可用时返回 ""。
    """
    if x is None or y is None:
        return ""
    seq = f"\x1b[{y + 1};{x + 1}H"
    seq += "\x1b[?25h" if visible else "\x1b[?25l"
    return seq


def render_plain(cells_rows) -> str:
    """可见屏幕纯文本：每行去尾空白，去掉末尾空行，行间 \\n

    直接消费引擎原生单元格元组（下标访问），避免中间对象。
    """
    lines = []
    for cells in cells_rows:
        lines.append("".join(c[_CELL_DATA] for c in cells).rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def render_ansi(cells_rows, include_cursor: bool = False, cursor=None) -> str:
    """可见屏幕 ANSI 渲染：每行前 CSI row+1;1H 定位，截断末尾空行

    每行前显式 CUP 定位到第 1 列，不依赖行末分隔符（比 ConPTY repaint
    更健壮，即使中间有空行也不会错位）。末尾可选追加光标定位序列。
    """
    line_results = []  # (row, rendered, has_content)
    last_non_empty = -1
    for row, cells in enumerate(cells_rows):
        line_parts = []
        last_sgr = ""
        has_content = False
        for cell in cells:
            if not cell[_CELL_DATA]:
                continue
            if not has_content and not is_default_cell(cell):
                has_content = True
            sgr = cell_to_sgr(cell)
            if sgr != last_sgr:
                line_parts.append(sgr)
                last_sgr = sgr
            line_parts.append(cell[_CELL_DATA])
        if last_sgr:
            line_parts.append("\x1b[0m")
        rendered = "".join(line_parts)
        line_results.append((row, rendered, has_content))
        if has_content:
            last_non_empty = len(line_results) - 1

    parts = []
    for i, (row, rendered, _) in enumerate(line_results):
        if i > last_non_empty:
            break  # 末尾空行，跳过
        parts.append(f"\x1b[{row + 1};1H")
        parts.append(rendered)
    if include_cursor and cursor is not None:
        parts.append(build_cursor_seq(*cursor))
    return "".join(parts)


def render_scrollback(cells_rows, keep_ansi: bool = False) -> str:
    """scrollback 历史区渲染

    keep_ansi=True: 每行 ANSI 内容 + \\r\\n（供前端推入 scrollback）。
    keep_ansi=False: 纯文本，每行去尾空白、剔除末尾空行、行间 \\n（与 render_plain 一致）。
    """
    if not cells_rows:
        return ""
    if not keep_ansi:
        return render_plain(cells_rows)
    parts = []
    for cells in cells_rows:
        line_parts = []
        last_sgr = ""
        for cell in cells:
            if not cell[_CELL_DATA]:
                continue
            sgr = cell_to_sgr(cell)
            if sgr != last_sgr:
                line_parts.append(sgr)
                last_sgr = sgr
            line_parts.append(cell[_CELL_DATA])
        if last_sgr:
            line_parts.append("\x1b[0m")
        parts.append("".join(line_parts))
        parts.append("\r\n")
    return "".join(parts)


class ScreenBackend:
    """终端模拟后端接口（wezterm 实现遵循的公共契约）

    后端只负责维护终端模型；可见区/scrollback 统一以
    List[List[cell-tuple]] 稀疏网格暴露，渲染由模块级函数完成。
    """

    name = "base"

    # —— 生命周期 ——
    def feed(self, data: bytes) -> None:  # pragma: no cover
        raise NotImplementedError

    def feed_text(self, text: str) -> None:  # pragma: no cover
        """feed ANSI 字符串（resize 重建用）"""
        raise NotImplementedError

    def resize(self, cols: int, rows: int) -> None:  # pragma: no cover
        raise NotImplementedError

    def reset(self) -> None:  # pragma: no cover
        raise NotImplementedError

    @property
    def available(self) -> bool:  # pragma: no cover
        raise NotImplementedError

    @property
    def cols(self) -> int:  # pragma: no cover
        raise NotImplementedError

    @property
    def rows(self) -> int:  # pragma: no cover
        raise NotImplementedError

    # —— 底层引擎访问 ——
    @property
    def emulator(self):  # pragma: no cover
        """底层终端模拟引擎对象（供输入编码共享模式状态；不适用时返回 None）"""
        raise NotImplementedError

    def drain_terminal_response(self) -> bytes:  # pragma: no cover
        """取走终端引擎生成的应答字节（终端查询如 DA1/CPR 的回复）

        feed() 处理后，引擎可能生成需回写子进程的应答序列（如 DA1/CPR/
        XTGETTCAP/OSC 颜色查询）。上层 reader 循环调用本方法取走并写回 PTY。
        不适用时返回 b""。
        """
        return b""

    # —— 数据查询 ——
    def cells(self):  # pragma: no cover
        """可见区稀疏网格：List[List[cell-tuple]]（引擎原生元组，勿构造中间对象）"""
        raise NotImplementedError

    def cursor(self):  # pragma: no cover
        """光标位置 (x, y, visible)，0-based"""
        raise NotImplementedError

    def capture_scrollback(self) -> str:  # pragma: no cover
        raise NotImplementedError

    def clear_scrollback(self) -> None:  # pragma: no cover
        raise NotImplementedError

    @property
    def scrollback_lines_count(self) -> int:  # pragma: no cover
        raise NotImplementedError


class WeztermBackend(ScreenBackend):
    """wezterm-py 后端（wezterm-term 终端模型）"""

    name = "wezterm"

    def __init__(self, cols: int, rows: int, hlimit: int = _DEFAULT_HLIMIT):
        self._cols = cols
        self._rows = rows
        self._hlimit = hlimit
        self._term = None
        if _HAS_WEZTERM:
            try:
                self._term = pywezterm.Terminal(cols=cols, rows=rows, scrollback=hlimit)
            except Exception as e:
                _logger.warning("WeztermBackend 初始化失败: %s", e)
                self._term = None

    @property
    def available(self) -> bool:
        return self._term is not None

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def emulator(self):
        """wezterm-py Terminal（输入编码与终端模型共享同一实例，模式状态一致）"""
        return self._term

    def drain_terminal_response(self) -> bytes:
        """取走 wezterm-term 生成的应答字节（DA1/CPR/XTGETTCAP/OSC 等查询回复）"""
        if self._term is None:
            return b""
        return self._term.drain_written()

    def feed(self, data: bytes) -> None:
        if self._term is None:
            return
        self._term.feed(data)

    def feed_text(self, text: str) -> None:
        if self._term is None or not text:
            return
        self._term.feed(text.encode("utf-8", "replace"))

    def resize(self, cols: int, rows: int) -> None:
        if self._term is None:
            return
        self._term.resize(cols, rows)
        self._cols = cols
        self._rows = rows

    def reset(self) -> None:
        if self._term is None:
            return
        self._term.reset()

    def cells(self):
        if self._term is None:
            return []
        # 直接返回引擎原生网格元组，渲染层按下标消费（避免逐 cell 转换）
        return self._term.snapshot()

    def cursor(self):
        if self._term is None:
            return (None, None, None)
        # wezterm 返回 (row, col, visible)
        y, x, visible = self._term.cursor()
        return (x, y, visible)

    def capture_scrollback(self, keep_ansi: bool = False) -> str:
        if self._term is None:
            return ""
        return render_scrollback(self._term.scrollback(), keep_ansi=keep_ansi)

    def clear_scrollback(self) -> None:
        if self._term is None:
            return
        self._term.clear_scrollback()

    @property
    def scrollback_lines_count(self) -> int:
        if self._term is None:
            return 0
        return self._term.scrollback_count()


def create_backend(
    cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS, hlimit: int = _DEFAULT_HLIMIT
):
    """创建 wezterm-py 终端模拟后端

    Returns:
        WeztermBackend 实例；wezterm-py 不可用时返回 None。
    """
    backend = WeztermBackend(cols, rows, hlimit)
    if backend.available:
        return backend
    return None
